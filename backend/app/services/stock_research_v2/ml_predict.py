"""Fixed-parameter multi-fold native training and separately requested evaluation.

This internal calculation service must run under the existing resource supervisor.
It does not create tasks, certify datasets, or register an unseen final test.
"""
from dataclasses import asdict
import hashlib
import json
import time

import pandas as pd

from app.schemas.stock_ml import ModelManifest, TrainingParameters
from app.schemas.stock_ml_experiment import WalkForwardSpec
from app.schemas.stock_research_v2 import DatasetRef, Scope
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.ml_dataset import frame_fingerprint
from app.services.stock_research_v2.ml_evaluate import evaluate_prediction
from app.services.stock_research_v2.ml_split import make_time_folds
from app.services.stock_research_v2.ml_train import fit_ranker, predict_ranker

METHOD_VERSION = "stock-walk-forward-calculation-v1"
SCORE_SEMANTICS = "ranking_score_not_probability_or_expected_return"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _records(frame):
    return [{"decision_date": str(row.decision_date.date()), "instrument_id": row.instrument_id,
        "score": float(row.score) if pd.notna(row.score) else None, "reason": row.reason} for row in frame.itertuples()]


def run_walk_forward_experiment(samples, spec, *, scope, dataset, feature_versions, parameters=None,
        qa_only=False, store=None, cancel_check=None, top_n=20):
    scope, dataset = Scope.model_validate(scope), DatasetRef.model_validate(dataset)
    spec, parameters = WalkForwardSpec.model_validate(spec), TrainingParameters.model_validate(parameters or {})
    if type(top_n) is not int or not 1 <= top_n <= 300:
        raise ValueError("候选数须为1至300")
    if store is not None and not isinstance(store, ArtifactStore):
        raise ValueError("须使用现有不可变产物存储")
    folds = make_time_folds(samples, spec)
    # A single parameter set is frozen before any fold trains; no terminal-test search.
    frozen = {"parameters": parameters.model_dump(mode="json"), "feature_versions": dict(feature_versions),
        "scope": scope.model_dump(mode="json"), "dataset": dataset.model_dump(mode="json"),
        "spec": spec.model_dump(mode="json"), "method_version": METHOD_VERSION}
    started = time.monotonic()
    def cancelled():
        if time.monotonic() - started > 600:
            raise JobError("execution_timeout", "全部滚动训练超过600秒预算", 504)
        return cancel_check is not None and cancel_check()
    reports = []
    for entry in folds:
        if cancelled():
            raise JobError("ml_cancelled", "滚动训练已取消", 409)
        model = fit_ranker(entry.fold, scope=scope, dataset=dataset, feature_versions=feature_versions,
            parameters=parameters, qa_only=qa_only, cancel_check=cancelled)
        prediction = predict_ranker(model, entry.fold.test_candidates, scope=scope, dataset=dataset, feature_versions=feature_versions)
        if cancelled():
            raise JobError("ml_cancelled", "滚动训练已取消，不发布结果", 409)
        selected = prediction.dropna(subset=["score"]).sort_values(["decision_date", "score", "instrument_id"],
            ascending=[True, False, True], kind="stable").groupby("decision_date", sort=False).head(top_n)
        report = {**entry.manifest, "model": model.manifest.model_dump(mode="json"), "predictions": _records(prediction),
            "prediction_sha256": frame_fingerprint(prediction, ["decision_date", "instrument_id", "score", "reason"]),
            "selected_candidates": _records(selected), "score_semantics": SCORE_SEMANTICS}
        if store is not None:
            report["saved_model"] = asdict(store.put_json({"kind": "stock_native_ranker_v1", "model": model.to_payload()}))
        reports.append(report)
    result = {**frozen, "parameter_freeze_sha256": _digest(frozen), "status": "exploratory_not_certified",
        "input_status": "qa_only" if qa_only else "unverified_calculation_inputs", "holdout_registry_verified": False,
        "score_semantics": SCORE_SEMANTICS, "top_n": top_n, "folds": reports, "evaluation": None,
        "portfolio_backtest": {"available": False, "reason": "verified_execution_and_benchmark_required"},
        "model_api_calls": 0, "limitations": ["最多5个时序fold，固定单组参数；未进行最终测试选参。",
            "后续fold仅可使用其当时已结束标签；先前测试窗口可能在后续训练历史内，最终窗口保持训练外。",
            "未接模型/最终测试登记、任务生命周期、来源资格或可成交组合，不认证独立样本外产品。"]}
    if store is not None:
        result["saved_report"] = asdict(store.put_json({"kind": METHOD_VERSION, "report": result}))
    return result


def evaluate_walk_forward(experiment, samples, *, scope, dataset, groups=5, ndcg_at=5):
    try:
        return _evaluate_walk_forward(experiment, samples, scope=scope, dataset=dataset, groups=groups, ndcg_at=ndcg_at)
    except JobError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise JobError("ml_experiment_integrity_failed", "实验内容、样本或评估输入无法校验", 409) from None


def _evaluate_walk_forward(experiment, samples, *, scope, dataset, groups, ndcg_at):
    scope, dataset = Scope.model_validate(scope), DatasetRef.model_validate(dataset)
    if not isinstance(experiment, dict) or experiment.get("scope") != scope.model_dump(mode="json") or experiment.get("dataset") != dataset.model_dump(mode="json"):
        raise JobError("ml_model_not_found", "实验不属于当前用户/盘/固定数据版本", 404)
    if experiment.get("method_version") != METHOD_VERSION or experiment.get("holdout_registry_verified") is not False:
        raise JobError("ml_experiment_integrity_failed", "实验版本或封存声明无效", 409)
    frozen = {name: experiment[name] for name in ["parameters", "feature_versions", "scope", "dataset", "spec", "method_version"]}
    if _digest(frozen) != experiment.get("parameter_freeze_sha256"):
        raise JobError("ml_experiment_integrity_failed", "冻结实验参数发生变化", 409)
    folds = make_time_folds(samples, experiment["spec"])
    reports = experiment.get("folds")
    if not isinstance(reports, list) or len(reports) != len(folds):
        raise JobError("ml_experiment_integrity_failed", "实验窗口数量不一致", 409)
    results, predictions, truths = [], [], []
    for entry, report in zip(folds, reports):
        if any(report.get(key) != value for key, value in entry.manifest.items()):
            raise JobError("ml_experiment_integrity_failed", "固定样本或fold发生变化", 409)
        model = ModelManifest.model_validate(report["model"])
        columns = ["decision_date", "instrument_id", "label_start", "label_end", "relevance", *entry.fold.feature_names]
        if (model.scope != scope or model.dataset != dataset or model.feature_order != list(entry.fold.feature_names)
                or model.feature_versions != experiment["feature_versions"] or model.parameters.model_dump(mode="json") != experiment["parameters"]
                or model.training_knowledge_cutoff >= entry.fold.spec.test_start
                or model.training_sha256 != frame_fingerprint(entry.fold.train, columns)
                or model.validation_sha256 != frame_fingerprint(entry.fold.validation, columns)):
            raise JobError("ml_experiment_integrity_failed", "模型身份、知识截止或参数与实验不一致", 409)
        prediction = pd.DataFrame(report["predictions"])
        prediction["decision_date"] = pd.to_datetime(prediction.decision_date)
        if frame_fingerprint(prediction, ["decision_date", "instrument_id", "score", "reason"]) != report.get("prediction_sha256"):
            raise JobError("ml_experiment_integrity_failed", "预测内容发生变化", 409)
        evaluated = evaluate_prediction(prediction, entry.fold.test_candidates, groups=groups, ndcg_at=ndcg_at)
        results.append({"fold_id": entry.fold_id, "test_role": entry.manifest["test_role"], **evaluated})
        predictions.append(prediction); truths.append(entry.fold.test_candidates)
    combined = evaluate_prediction(pd.concat(predictions, ignore_index=True), pd.concat(truths, ignore_index=True), groups=groups, ndcg_at=ndcg_at)
    return {**combined, "fold_evaluations": results, "parameter_freeze_sha256": experiment["parameter_freeze_sha256"],
        "terminal_test_evaluated": True, "holdout_consumption_registered": False}
