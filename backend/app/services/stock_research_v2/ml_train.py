"""Internal CPU LambdaRank kernel. Production callers must use bounded task execution."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import platform
import re
import sys
import time

import numpy as np
import pandas as pd

from app.schemas.stock_ml import ModelManifest, TrainingParameters
from app.schemas.stock_research_v2 import Scope, DatasetRef, SHA256
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.ml_dataset import MLFold, frame_fingerprint
from app.services.stock_research_v2.qualification import allows

MAX_MODEL_BYTES = 8 * 1024 * 1024


def engine():
    try:
        import lightgbm as lgb
        runtime = {name: importlib.metadata.version(name) for name in ["lightgbm", "narwhals", "numpy", "scipy", "pandas"]}
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise JobError("ml_engine_unavailable", "独立股票ML依赖未就绪", 503) from None
    if runtime["lightgbm"] != "4.7.0" or runtime["narwhals"] != "2.27.1":
        raise JobError("ml_engine_unavailable", "股票排序引擎与锁版不一致", 503)
    return lgb, {**runtime, "python": platform.python_version(), "platform": sys.platform}


@dataclass(frozen=True)
class TrainedRanker:
    model_text: str
    manifest: ModelManifest

    def to_payload(self):
        return {"model_text": self.model_text, "manifest": self.manifest.model_dump(mode="json")}


def identity(scope, dataset):
    return Scope.model_validate(scope), DatasetRef.model_validate(dataset)


def fit_ranker(fold, *, scope, dataset, feature_versions, parameters=None, qa_only=False, cancel_check=None):
    scope, dataset = identity(scope, dataset)
    parameters = TrainingParameters.model_validate(parameters or {})
    if type(qa_only) is not bool or (not qa_only and not allows(dataset.qualification, "ml")):
        raise JobError("dataset_not_qualified", "观察数据不能进入正式ML训练；QA模式不能作为真实研究验收", 422)
    if (not isinstance(fold, MLFold) or not isinstance(feature_versions, dict)
            or len(set(fold.feature_names)) != len(fold.feature_names) or set(feature_versions) != set(fold.feature_names)
            or any(not isinstance(value, str) or not re.fullmatch(SHA256, value) for value in feature_versions.values())):
        raise JobError("ml_feature_mismatch", "特征顺序或固定版本不一致", 409)
    if len(fold.feature_names) > 16 or sum(len(frame) for frame in [fold.train, fold.validation]) * len(fold.feature_names) > 2_000_000:
        raise JobError("ml_budget_exceeded", "训练样本超过单任务预算", 422)
    if fold.train.empty or fold.validation.empty or fold.train.label_end.max().date() >= fold.spec.validation_start or fold.validation.label_end.max().date() >= fold.spec.test_start:
        raise JobError("ml_label_leakage", "标签窗口跨越后续验证或测试边界", 422)
    for frame in [fold.train, fold.validation]:
        if any(frame[name].isna().any() or not pd.api.types.is_datetime64_dtype(frame[name]) for name in ["decision_date", "label_start", "label_end"]) or not ((frame.decision_date < frame.label_start) & (frame.label_start < frame.label_end)).all():
            raise JobError("ml_label_invalid", "日期或标签可获得窗口无效", 422)
        if frame.duplicated(["decision_date", "instrument_id"]).any() or not frame[["decision_date", "instrument_id"]].reset_index(drop=True).equals(frame.sort_values(["decision_date", "instrument_id"], kind="stable")[["decision_date", "instrument_id"]].reset_index(drop=True)):
            raise JobError("ml_group_mismatch", "日期query组必须稳定排序且不能重复", 422)
        if frame.relevance.isna().any() or not frame.relevance.between(0, 4).all() or not (frame.relevance % 1 == 0).all():
            raise JobError("ml_label_invalid", "排序等级无效，未补造标签", 422)
        queries = frame.groupby("decision_date").agg(n=("instrument_id", "size"), levels=("relevance", "nunique"))
        if (queries.n < fold.min_group_size).any() or (queries.levels < 2).any() or (frame is fold.train and len(queries) < 2):
            raise JobError("ml_group_mismatch", "有效日期query组不足", 422)
        matrix = frame[list(fold.feature_names)].to_numpy(dtype=float)
        if np.isinf(matrix).any() or not np.isfinite(matrix).any(axis=1).all():
            raise JobError("ml_feature_invalid", "训练特征非有限或全部缺失", 422)
    lgb, runtime = engine()
    names = [f"f{index:03d}" for index in range(len(fold.feature_names))]
    def native(frame, reference=None):
        return lgb.Dataset(frame[list(fold.feature_names)].to_numpy(dtype=float), label=frame.relevance.to_numpy(dtype=int),
            group=frame.groupby("decision_date", sort=False).size().to_numpy(), feature_name=names, reference=reference, free_raw_data=False)
    train = native(fold.train)
    validation = native(fold.validation, train)
    start = time.monotonic()
    def budget(env):
        if cancel_check is not None and cancel_check():
            raise JobError("ml_cancelled", "排序训练已取消", 409)
        if time.monotonic() - start > parameters.max_seconds:
            raise JobError("execution_timeout", "排序训练超时", 504)
    budget.before_iteration, budget.order = True, 0
    p = parameters
    options = {"objective": "lambdarank", "metric": "ndcg", "ndcg_eval_at": [5], "label_gain": [0, 1, 3, 7, 15],
        "device_type": "cpu", "num_threads": 2, "deterministic": True, "force_col_wise": True, "verbosity": -1,
        "seed": p.seed, "data_random_seed": p.seed, "feature_fraction_seed": p.seed, "bagging_seed": p.seed, "drop_seed": p.seed,
        "max_depth": p.max_depth, "num_leaves": p.num_leaves, "min_data_in_leaf": p.min_child_samples,
        "learning_rate": p.learning_rate, "lambda_l1": p.reg_alpha, "lambda_l2": p.reg_lambda}
    booster = lgb.train(options, train, num_boost_round=p.n_estimators, valid_sets=[validation], valid_names=["validation"],
        callbacks=[budget, lgb.early_stopping(10, verbose=False)])
    used = booster.best_iteration or booster.current_iteration()
    text = booster.model_to_string(num_iteration=used)
    if len(text.encode()) > MAX_MODEL_BYTES:
        raise JobError("ml_model_budget_exceeded", "模型超过产物预算", 422)
    columns = ["decision_date", "instrument_id", "label_start", "label_end", "relevance", *fold.feature_names]
    manifest = ModelManifest(scope=scope, dataset=dataset, parameters=p, runtime=runtime, feature_order=list(fold.feature_names), feature_versions=feature_versions,
        training_sha256=frame_fingerprint(fold.train, columns), validation_sha256=frame_fingerprint(fold.validation, columns),
        model_sha256=hashlib.sha256(text.encode()).hexdigest(), training_knowledge_cutoff=max(fold.train.label_end.max(), fold.validation.label_end.max()).date(),
        created_at=datetime.now(timezone.utc), trees_used=used, elapsed_seconds=time.monotonic()-start,
        feature_gain=dict(zip(fold.feature_names, map(float, booster.feature_importance(importance_type="gain")))), validation_status="qa_only" if qa_only else "research_only")
    return TrainedRanker(text, manifest)


def restore_ranker(payload, *, scope, dataset):
    scope, dataset = identity(scope, dataset)
    try:
        if not isinstance(payload, dict) or set(payload) != {"model_text", "manifest"}:
            raise ValueError()
        manifest = ModelManifest.model_validate(payload["manifest"])
        text = payload["model_text"]
        if manifest.scope != scope or manifest.dataset != dataset:
            raise JobError("ml_model_not_found", "模型不属于当前用户/盘/固定数据版本", 404)
        if not isinstance(text, str) or not 0 < len(text.encode()) <= MAX_MODEL_BYTES or hashlib.sha256(text.encode()).hexdigest() != manifest.model_sha256:
            raise ValueError()
        _, runtime = engine()
        if manifest.runtime != runtime:
            raise ValueError()
        return TrainedRanker(text, manifest)
    except JobError:
        raise
    except (ValueError, TypeError, KeyError):
        raise JobError("ml_model_integrity_failed", "模型内容、版本或运行环境不一致", 409) from None


def predict_ranker(model, candidates, *, scope, dataset, feature_versions):
    model = restore_ranker(model.to_payload(), scope=scope, dataset=dataset)
    manifest = model.manifest
    if not isinstance(feature_versions, dict) or feature_versions != manifest.feature_versions:
        raise JobError("ml_feature_mismatch", "推理特征顺序/版本不匹配", 409)
    if not isinstance(candidates, pd.DataFrame) or candidates.empty or len(candidates) * len(feature_versions) > 2_000_000:
        raise JobError("ml_budget_exceeded", "推理候选为空或超过单任务预算", 422)
    if (candidates.decision_date.dt.date <= manifest.training_knowledge_cutoff).any():
        raise JobError("ml_prediction_not_oos", "预测日期不得早于模型训练和验证标签知识截止", 422)
    matrix = candidates[manifest.feature_order].to_numpy(dtype=float)
    if np.isinf(matrix).any():
        raise JobError("ml_feature_invalid", "推理特征非有限", 422)
    usable = np.isfinite(matrix).any(axis=1)
    result = candidates[["decision_date", "instrument_id"]].copy()
    result["score"] = np.nan
    if usable.any():
        lgb, _ = engine()
        native = lgb.Booster(model_str=model.model_text)
        scores = native.predict(matrix[usable], num_iteration=manifest.trees_used, num_threads=2)
        if not np.isfinite(scores).all():
            raise JobError("ml_prediction_invalid", "模型输出非有限分数", 409)
        result.loc[usable, "score"] = scores
    result["reason"] = np.where(usable, None, "missing_all_features")
    return result
