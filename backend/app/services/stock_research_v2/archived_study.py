"""Internal fixed-artifact business orchestration, gated until real data certification.

This deliberately has no HTTP mount or automatic worker lifecycle. Call only in a
bounded job after production permission/qualification and migrations are verified.
"""
from dataclasses import asdict
import logging

from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import descriptor
from app.services.stock_research_v2.columnar import ColumnarService
from app.services.stock_research_v2.custom_factors import CustomFactorService
from app.services.stock_research_v2.factor_values import resolve_factors, DEPENDENCIES
from app.services.stock_factors.engine import PreprocessConfig
from app.services.stock_research_v2.research_experiment import run_experiment
from app.services.stock_research_v2.study_inputs import load_study_inputs

REPORT_VERSION = "stock-archived-factor-ml-study-v1"
logger = logging.getLogger("qihuo.stock_archived_study")


async def run_archived_study(registry, receipt, *, calendar, universe, builtin_ids, custom_refs, split, request_id,
        horizon=20, groups=5, top_n=20, config=None, training_parameters=None, qa_only=False):
    if not isinstance(registry, ColumnarService):
        raise JobError("invalid_study_context", "需要绑定本人/盘的固定列式服务", 422)
    # The registry owns the permission/version check; never trust the ref's qualification.
    await registry.read(receipt)
    if type(qa_only) is not bool or not qa_only:
        raise JobError("dataset_not_qualified", "固定观察研究尚未正式认证，不开放生产ML；仅内部显式QA可运行", 422)
    if config is not None and not isinstance(config, PreprocessConfig):
        raise JobError("invalid_preprocessing", "预处理必须为明确类型配置", 422)
    service = CustomFactorService(registry.session, registry.scope.owner_id, registry.scope.trading_mode)
    factors = await resolve_factors(service, builtin_ids, custom_refs)
    fields = {"close"}
    for item in factors:
        fields.update(item.expression.fields if item.expression else [DEPENDENCIES[item.key]])
    if config is not None and config.industry_neutral:
        raise JobError("historical_industry_unavailable", "固定观察归档没有可认证历史行业，未用当前行业替代", 422)
    if config is not None and config.mktcap_neutral:
        fields.add("marketcap")
    inputs = await load_study_inputs(registry, receipt, calendar=calendar, universe=universe, fields=sorted(fields), request_id=request_id)
    store = registry.archive.require_store()
    study = run_experiment(inputs.fields, factors, scope=registry.scope, dataset=receipt.dataset, split=split,
        horizon=horizon, groups=groups, top_n=top_n, config=config, eligible=inputs.eligible,
        training_parameters=training_parameters, qa_only=True, store=store)
    payload = {"report_version": REPORT_VERSION, "validation_status": "qa_only", "inputs": inputs.metadata, "study": study,
        "persistence": "immutable_local_artifacts_not_authorized_result_registry"}
    # Source content + full calendar/pool content are bound in inputs.input_sha256.
    payload["semantic_sha256"], _ = descriptor({"study": study["semantic_sha256"], "inputs": inputs.metadata}, 4*1024*1024)
    payload["saved_report"] = asdict(store.put_json(payload))
    logger.info("request_id=%s user_id=%s mode=%s operation=archived_study_saved data_version=%s report_sha256=%s model_calls=0 qa_only=true", request_id, registry.scope.owner_id, registry.scope.trading_mode, receipt.dataset.data_version, payload["saved_report"]["sha256"])
    return payload
