"""Connected calculation study. No public endpoint or verified execution claim."""
from dataclasses import asdict
import hashlib
import json

import pandas as pd

from app.schemas.stock_ml import TimeSplit
from app.schemas.stock_research_v2 import Scope, DatasetRef
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.analysis import analyze_values, forward_returns
from app.services.stock_research_v2.artifacts import ArtifactStore, ArtifactError
from app.services.stock_research_v2.factor_values import compute_factors
from app.services.stock_factors.engine import PreprocessConfig
from app.services.stock_research_v2.ml_dataset import build_ml_dataset, split_samples
from app.services.stock_research_v2.ml_train import fit_ranker, predict_ranker, restore_ranker

REPORT_VERSION = "stock-factor-ml-calculation-study-v1"


def run_experiment(fields, factors, *, scope, dataset, split, horizon=20, groups=5, top_n=20, config=None, industry=None, eligible=None,
        training_parameters=None, qa_only=False, store=None):
    scope, dataset, split = Scope.model_validate(scope), DatasetRef.model_validate(dataset), TimeSplit.model_validate(split)
    if type(top_n) is not int or not 1 <= top_n <= 300:
        raise ValueError("候选持股数量须为1至300")
    if dataset.qualification == "observation_only" and not qa_only:
        raise JobError("dataset_not_qualified", "观察归档不能直接成为正式研究输入", 422)
    config = config or PreprocessConfig()
    bundle = compute_factors(fields, factors, config=config, industry=industry, eligible=eligible)
    features = {item.key: bundle.values[item.key] * (1 if item.direction == "high" else -1) for item in bundle.definitions}
    pipeline = {"factor_method": bundle.method_version, "preprocessing": asdict(config), "label_basis": "price_return_next_close_no_corporate_actions_unverified"}
    versions = {item.key: hashlib.sha256(json.dumps({**pipeline, "definition_version": item.version}, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for item in bundle.definitions}
    close = fields["close"]
    target = forward_returns(close, horizon)
    dates = close.index
    windows = {date: (dates[index+1], dates[index+horizon+1]) if index+horizon+1 < len(dates) else None for index, date in enumerate(dates)}
    samples = build_ml_dataset(features, target, windows)
    fold = split_samples(samples, split)
    model = fit_ranker(fold, scope=scope, dataset=dataset, feature_versions=versions, parameters=training_parameters, qa_only=qa_only)
    predictions = predict_ranker(model, fold.test_candidates, scope=scope, dataset=dataset, feature_versions=versions)
    # Report training-period efficacy only; final test labels are not an evaluation input.
    history = (close.index >= pd.Timestamp(split.train_start)) & (close.index <= pd.Timestamp(split.train_end))
    analyses = {item.key: analyze_values(bundle.values[item.key].loc[history], close.loc[history], direction=item.direction,
        horizon=horizon, groups=groups, decay_lags=[1, horizon]) for item in bundle.definitions}
    def records(frame):
        return [{"date": str(row.decision_date.date()), "instrument_id": row.instrument_id,
            "score": float(row.score) if pd.notna(row.score) else None, "reason": row.reason} for row in frame.itertuples()]
    selected = predictions.dropna(subset=["score"]).sort_values(["decision_date", "score", "instrument_id"], ascending=[True, False, True], kind="stable").groupby("decision_date", sort=False).head(top_n)
    report = {"report_version": REPORT_VERSION, "validation_status": "qa_only" if qa_only else "calculation_only_unverified_inputs",
        "scope": scope.model_dump(mode="json"), "dataset": dataset.model_dump(mode="json"), "split": split.model_dump(mode="json"),
        "horizon": horizon, "groups": groups, "top_n": top_n, "factor_versions": [{"key": item.key, "version": item.version, "direction": item.direction} for item in bundle.definitions],
        **pipeline, "factor_analysis_period": {"start": str(split.train_start), "end": str(split.train_end)},
        "factor_coverage": bundle.coverage, "factor_analysis": analyses, "purged_samples": fold.purge_counts,
        "candidate_count": len(fold.test_candidates), "model": model.manifest.model_dump(mode="json"),
        "predictions": records(predictions), "selected_candidates": records(selected),
        "portfolio_backtest": {"available": False, "reason": "verified_execution_and_benchmark_required"},
        "limitations": ["计算链不认证来源/PIT/日历/历史股票池，本次仍需独立实库和真实数据验收。", "候选排序不是成交结果，未输出免成本的伪回测。", "测试封存登记尚未接入，不声明独立样本外验收，未用测试标签选择训练参数。"]}
    semantic = {**report, "model": {key: value for key, value in report["model"].items() if key not in {"created_at", "elapsed_seconds"}}}
    report["semantic_sha256"] = hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    if store is not None:
        if not isinstance(store, ArtifactStore):
            raise ValueError("研究产物必须使用既有不可变内容存储")
        report["saved_model"] = asdict(store.put_json({"kind": "stock_native_ranker_v1", "model": model.to_payload()}))
        report["saved_report"] = asdict(store.put_json({"kind": REPORT_VERSION, "report": report}))
    return report


def load_experiment_model(store, sha256, *, scope, dataset):
    if not isinstance(store, ArtifactStore):
        raise ValueError("研究模型存储无效")
    try:
        payload = store.read_json(sha256)
        if not isinstance(payload, dict) or set(payload) != {"kind", "model"} or payload["kind"] != "stock_native_ranker_v1":
            raise ValueError()
        return restore_ranker(payload["model"], scope=scope, dataset=dataset)
    except (ValueError, ArtifactError):
        raise JobError("ml_model_integrity_failed", "模型产物缺失或无法校验", 409) from None
