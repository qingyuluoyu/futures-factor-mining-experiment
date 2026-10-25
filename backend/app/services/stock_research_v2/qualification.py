from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from app.schemas.stock_dataset_inspection import ResearchRequirements
from app.services.stock_research_v2.custom_factors import CustomFactorService
from app.services.stock_research_v2.panels import eligible_value

METHOD_VERSION = "stock-observation-requirements-v1"
DEPENDENCIES = {"pe_ttm": ("pe_ttm", 0), "pb": ("pb", 0), "revenue_yoy": ("revenue_yoy", 0), "netprofit_yoy": ("netprofit_yoy", 0), "roe": ("roe", 0), "gross_margin": ("gross_margin", 0), "ret_20d": ("close", 20), "vol_20d": ("close", 20), "turnover_20d": ("turnover", 19)}


async def inspect_requirements(archive, reference, request: ResearchRequirements):
    request = ResearchRequirements.model_validate(request.model_dump())
    manifest = await archive.read_dataset(reference)
    required, lookback, custom = set(), 0, []
    for factor in request.factor_ids:
        field, history = DEPENDENCIES[factor]
        required.add(field)
        lookback = max(lookback, history)
    factors = CustomFactorService(archive.session, archive.scope.owner_id, archive.scope.trading_mode)
    for fixed in request.custom_factors:
        compiled = await factors.resolve(**fixed.model_dump())
        required.update(compiled.fields)
        lookback = max(lookback, compiled.lookback)
        custom.append(fixed.model_dump(mode="json"))
    if request.mktcap_neutral or request.weight == "mktcap":
        required.add("marketcap")
    needs_industry = request.industry_neutral or request.action == "rotation"
    if needs_industry:
        required.add("industry")
    fields = {field: {"raw_observations": 0, "eligible_observations": 0, "source_null": 0, "excluded_domain": 0, "basis_unverified": 0, "temporal_unverified": 0} for field in sorted(required)}
    start, end = date.fromisoformat(manifest["warmup_start"]), date.fromisoformat(manifest["end"])
    decision = datetime.combine(end, time(15), ZoneInfo("Asia/Shanghai"))
    instruments = set(manifest["instruments"])
    for source, artifact in await archive.source_rows([UUID(item["source_id"]) for item in manifest["sources"]]):
        page = archive.verified_source(source, artifact)
        for row in page.observations:
            if row.instrument_id not in instruments or (page.kind != "financial" and not start <= row.effective_date <= end):
                continue
            for field in required & row.values.keys():
                stats, value = fields[field], row.values[field]
                stats["raw_observations"] += 1
                if value is None:
                    stats["source_null"] += 1
                elif eligible_value(field, value) is None:
                    stats["excluded_domain"] += 1
                elif page.kind == "financial" and page.financial_basis != "consolidated":
                    stats["basis_unverified"] += 1
                elif page.kind == "financial" and (row.announced_at is None or row.available_at is None or row.available_at > decision or row.vintage == "unverified" or row.vintage_reference is None):
                    stats["temporal_unverified"] += 1
                else:
                    stats["eligible_observations"] += 1
    missing = [field for field, stats in fields.items() if stats["eligible_observations"] == 0]
    blockers = ["observation_only_dataset", "calendar_unverified", "historical_universe_unverified", "source_authorization_evidence_unverified"]
    if missing:
        blockers.append("selected_fields_unavailable")
    if needs_industry:
        blockers.append("historical_industry_unavailable")
    if request.action in ("backtest", "rotation"):
        blockers.append("execution_evidence_unavailable")
    return {"method_version": METHOD_VERSION, "dataset": reference.model_dump(mode="json"), "qualification": "observation_only", "research_enabled": False,
        "required_fields": sorted(required), "missing_fields": missing, "fields": fields, "lookback": lookback, "custom_factors": custom,
        "blocking_reasons": blockers, "coverage_basis": "source_observations_not_trading_grid", "warnings": ["记录存在不代表跨日期/证券完整覆盖，未以同源一致认证真实性。", "未核实字段可得时间与所需全部依赖前，不运行IC或回测。"]}
