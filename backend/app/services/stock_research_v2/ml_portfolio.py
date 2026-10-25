"""Immutable native ranking experiment -> the same stock execution ledger.

Internal orchestration only. Raw execution evidence remains independently unverified;
there is no live account write, model confirmation or implicit test evaluation.
"""
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import logging
import math
import re

import pandas as pd

from app.schemas.stock_ml import ModelManifest, TimeSplit
from app.schemas.stock_portfolio_v2 import Candidate, PortfolioInputs, PortfolioRequest, SignalSnapshot
from app.schemas.stock_research_v2 import explicit_date
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import ArchiveService, descriptor
from app.services.stock_research_v2.ml_dataset import frame_fingerprint
from app.services.stock_research_v2.ml_predict import METHOD_VERSION, SCORE_SEMANTICS
from app.services.stock_research_v2.portfolio import simulate_portfolio
from app.services.stock_research_v2.research_experiment import load_experiment_model

REPORT_VERSION = "stock-ml-execution-calculation-v1"
logger = logging.getLogger("qihuo.stock_ml_portfolio")


def _date(value):
    explicit_date(value)
    return date.fromisoformat(value) if isinstance(value, str) else value


async def run_ml_portfolio(archive, experiment_sha256, request, market, *, request_id):
    if not isinstance(archive, ArchiveService) or not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise JobError("invalid_study_context", "ML组合需要当前用户/盘和请求标识", 422)
    request = PortfolioRequest.model_validate(request.model_dump() if isinstance(request, PortfolioRequest) else request)
    if request.scope != archive.scope:
        raise JobError("ml_model_not_found", "ML组合不属于当前用户/盘", 404)
    parent = await archive.read_dataset(request.dataset)
    if request.pool_version != parent["pool_version"]:
        raise JobError("ml_portfolio_version_conflict", "组合历史股票池与固定数据版本不一致", 409)
    if request.weight == "mktcap":
        raise JobError("ml_marketcap_required", "模型分数不含当时市值；尚未连接固定PIT市值，未补0或1", 422)
    if not isinstance(market, dict) or set(market) - {"calendar", "bars", "evidence", "events"}:
        raise JobError("invalid_study_context", "成交输入只接受明确日历/原价/事件与证据，不接受外部信号覆盖", 422)
    store = archive.require_store()
    try:
        document = store.read_json(experiment_sha256)
        if set(document) != {"kind", "report"} or document["kind"] != METHOD_VERSION:
            raise ValueError()
        experiment = document["report"]
        if experiment["scope"] != request.scope.model_dump(mode="json") or experiment["dataset"] != request.dataset.model_dump(mode="json"):
            raise JobError("ml_model_not_found", "模型实验不属于当前用户/盘/固定数据版本", 404)
        if experiment["method_version"] != METHOD_VERSION or experiment["score_semantics"] != SCORE_SEMANTICS or experiment["holdout_registry_verified"] is not False:
            raise ValueError()
        frozen = {name: experiment[name] for name in ["parameters", "feature_versions", "scope", "dataset", "spec", "method_version"]}
        if descriptor(frozen, 4*1024*1024)[0] != experiment["parameter_freeze_sha256"] or not 1 <= len(experiment["folds"]) <= 5:
            raise ValueError()
        if sum(len(fold["predictions"]) for fold in experiment["folds"]) > 2_000_000:
            raise ValueError()
        grouped, sources = {}, []
        missing_scores = 0
        for fold in experiment["folds"]:
            model = ModelManifest.model_validate(fold["model"])
            if not request.qa_only and model.validation_status == "qa_only":
                raise JobError("ml_model_not_qualified", "QA模型不能变为正式组合模型", 422)
            restored = load_experiment_model(store, fold["saved_model"]["sha256"], scope=request.scope, dataset=request.dataset)
            split = TimeSplit.model_validate(fold["split"])
            if restored.manifest != model or model.feature_versions != experiment["feature_versions"] or model.parameters.model_dump(mode="json") != experiment["parameters"] or model.training_knowledge_cutoff >= split.test_start:
                raise ValueError()
            records = fold["predictions"]
            for record in records:
                if set(record) != {"decision_date", "instrument_id", "score", "reason"}:
                    raise ValueError()
                day = _date(record["decision_date"])
                if not split.test_start <= day <= split.test_end or day <= model.training_knowledge_cutoff:
                    raise ValueError()
                score = record["score"]
                if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
                    raise ValueError()
                if record["reason"] not in {None, "missing_all_features"} or (score is None) != (record["reason"] == "missing_all_features"):
                    raise ValueError()
            prediction = pd.DataFrame(records)
            prediction["decision_date"] = pd.to_datetime(prediction.decision_date)
            if prediction.duplicated(["decision_date", "instrument_id"]).any() or frame_fingerprint(prediction, ["decision_date", "instrument_id", "score", "reason"]) != fold["prediction_sha256"]:
                raise ValueError()
            source = {"fold_id": fold["fold_id"], "model_sha256": model.model_sha256,
                "feature_versions": model.feature_versions, "parameter_freeze_sha256": experiment["parameter_freeze_sha256"], "prediction_sha256": fold["prediction_sha256"]}
            version, _ = descriptor(source, 128*1024)
            sources.append(source)
            for day, rows in prediction.groupby("decision_date", sort=True):
                decision = day.date()
                if not request.start <= decision < request.end:
                    continue
                if decision in grouped:
                    raise ValueError()
                available = rows.dropna(subset=["score"])
                if available.empty:
                    raise JobError("ml_scores_unavailable", "所选交易日全部缺预测，不把无预测变为卖出信号", 422)
                missing_scores += len(rows) - len(available)
                grouped[decision] = SignalSnapshot(decision_date=decision, signal_version=version, direction="high",
                    candidates=[Candidate(instrument_id=row.instrument_id, score=Decimal(str(row.score))) for row in available.itertuples()])
        expected = {_date(day) for day in market["calendar"] if request.start <= _date(day) < request.end}
        if not expected or set(grouped) != expected:
            raise JobError("ml_prediction_coverage_gap", "组合区间不在完整预测交易日内，未拼接优胜窗口或跳日", 422)
        inputs = PortfolioInputs.model_validate({**market, "signals": [grouped[day] for day in sorted(grouped)]})
    except JobError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise JobError("ml_portfolio_integrity_failed", "模型/预测/日期/输入完整性无法核验", 409) from None
    portfolio = simulate_portfolio(request, inputs)
    report = {"report_version": REPORT_VERSION, "validation_status": "qa_only" if request.qa_only else "execution_calculation_only",
        "experiment_sha256": experiment_sha256, "prediction_sources": sources, "missing_scores_excluded": missing_scores,
        "portfolio": portfolio.model_dump(mode="json"), "holdout_registry_verified": False,
        "limitations": ["仅计算连接，原价/事件/PIT证据和正式模型/测试消费登记仍未认证。", "ML分数只决定排序；使用同一股票成交引擎，不使用未来收益补选。", "独立样本外、真实基准和原系统上线验收仍未通过；此调用不隐式评价最终测试。"]}
    report["saved_report"] = asdict(store.put_json(report))
    logger.info("request_id=%s user_id=%s mode=%s operation=ml_portfolio_saved status=%s report_sha256=%s model_calls=0", request_id, archive.scope.owner_id, archive.scope.trading_mode, portfolio.status, report["saved_report"]["sha256"])
    return report
