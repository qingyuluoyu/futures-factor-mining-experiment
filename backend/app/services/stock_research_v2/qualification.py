"""Evidence-graded qualification for fixed stock research datasets.

The grade is a pure function of rule-checkable evidence.  There is no admin button,
no request flag and no manifest edit that can raise it: a dataset only reaches
``research_only`` or ``backtest_ready`` when every prerequisite of that level is
evidenced by a version-bound record.  Missing evidence lowers the grade and names
the reason, and a capability whose own dependency has no eligible data is refused
outright instead of being silently downgraded or dropped.

``inspect_requirements`` reports the grade a dataset *could* reach from the evidence
supplied.  The registered row keeps its stored grade until an authorized
certification re-registers it; migration 020 only widens what may be stored.
"""
from datetime import date, datetime, time
import hashlib
from pathlib import Path
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, text

from app.schemas.stock_dataset_inspection import CapabilityAssessment, ResearchRequirements, SourceAuthorizationEvidence
from app.schemas.stock_portfolio_v2 import ExecutionEvidence
from app.services.stock_research_v2.custom_factors import CustomFactorService
from app.services.stock_research_v2.panels import eligible_value
from app.services.stock_research_v2.point_in_time import TradingCalendar
from app.services.stock_research_v2.universe import HistoricalUniverse

METHOD_VERSION = "stock-observation-requirements-v2"
DEPENDENCIES = {"pe_ttm": ("pe_ttm", 0), "pb": ("pb", 0), "revenue_yoy": ("revenue_yoy", 0), "netprofit_yoy": ("netprofit_yoy", 0), "roe": ("roe", 0), "gross_margin": ("gross_margin", 0), "ret_20d": ("close", 20), "vol_20d": ("close", 20), "turnover_20d": ("turnover", 19)}

QUALIFICATIONS = ("observation_only", "research_only", "backtest_ready")
CAPABILITIES = ("analysis", "portfolio", "ml", "rotation")
CAPABILITY_FLOOR = {"analysis": "research_only", "ml": "research_only", "rotation": "research_only", "portfolio": "backtest_ready"}
PRICE_FIELDS = ("open", "high", "low", "close")
VOLUME_FIELDS = ("volume", "amount")
ADJUSTMENT_FIELD = "adj_factor"
INDUSTRY_FIELD = "industry"
EXECUTION_REFERENCES = ("calendar_reference", "raw_prices_reference", "status_and_limits_reference",
    "corporate_actions_reference", "delistings_reference", "historical_universe_reference")
ADJUSTMENT_BASES = ("raw", "qfq")

# A gap in any of these means the observation itself is not a certified research input.
OBSERVATION_REASONS = frozenset({"observation_only_dataset", "calendar_unverified", "historical_universe_unverified",
    "source_authorization_evidence_unverified", "selected_fields_unavailable", "historical_industry_unavailable"})
# A gap in any of these means the input may be researched but never traded as-is.
RESEARCH_REASONS = frozenset({"execution_evidence_unavailable", "adjustment_basis_unverified",
    "price_volume_fields_unavailable", "corporate_action_economic_results_unavailable", "delisting_economic_results_unavailable"})
# Every reason a caller is allowed to report.  Anything outside this set is a wiring
# bug in the caller, not a pass: derive_qualification refuses it instead of guessing.
KNOWN_REASONS = OBSERVATION_REASONS | RESEARCH_REASONS

CAPABILITY_LIMITATIONS = {
    "analysis": ["因子IC/衰减/分组仅为固定样本内计算，不认证来源、时点或历史股票池。", "分组收益不含成本与成交约束，不是可交易结果。"],
    "portfolio": ["日线收盘决策、次日开盘成交的保守模拟，未接真实资金。", "成交价、涨跌停、公司行动与退市经济结果须由可信适配器另行提供。"],
    "ml": ["排序模型只对固定样本外候选打分，不构成交易信号或独立样本外验收。", "特征缺失必须显式报错，不静默剔除特征或样本。"],
    "rotation": ["行业轮动依赖可认证历史行业分类，未提供时不用当前行业回填。", "轮换基准、频率与成本口径须与组合口径一致后才可比较。"],
}

SCHEMA_VERSION = "20261025_020_stock_research_qualification"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / f"{SCHEMA_VERSION}.sql"
VERSION_TABLE = "stock_research_qualification_schema_versions"
DATASET_TABLE = "stock_research_datasets"
GRADE_CHECK = "ck_stock_dataset_qualification"
GRADE_COLUMN = "qualification"


def derive_qualification(reasons):
    """Highest grade whose prerequisites are all evidenced; never higher.

    ``:detail`` suffixes only name the offending field or record, they never
    change the tier.  A reason that is not registered in either tier raises
    instead of being ignored: silently dropping it would turn an unknown blocker
    into a pass, and this function is the single authority every capability gate
    reads.  Callers therefore cannot open a gate by inventing a reason name.
    """
    tiers = {str(reason).split(":", 1)[0] for reason in reasons or ()}
    unrecognised = sorted(tiers - KNOWN_REASONS)
    if unrecognised:
        raise ValueError(f"stock qualification blocking reason is unrecognised: {', '.join(unrecognised)}")
    if tiers & OBSERVATION_REASONS:
        return "observation_only"
    if tiers & RESEARCH_REASONS:
        return "research_only"
    return "backtest_ready"


def allows(qualification, capability):
    """Single gate authority shared by every downstream capability."""
    floor = CAPABILITY_FLOOR.get(capability)
    return floor is not None and qualification in QUALIFICATIONS and QUALIFICATIONS.index(qualification) >= QUALIFICATIONS.index(floor)


def grade_allowance(expression):
    """Canonical ``(column, sorted allowed grades)`` for the grade CHECK.

    PostgreSQL renders ``x IN (...)`` as ``x = ANY (ARRAY[...])`` while SQLite
    reflects the DDL verbatim, so both spellings reduce to the same set instead
    of being compared as raw text.

    Two decorations that appear only on PostgreSQL must be peeled off before the
    allowlist can be read: ``pg_get_constraintdef`` prefixes the definition with
    ``CHECK`` and wraps the expression in one extra pair of parens.  Leaving either
    in place puts a ``(`` at position 0, where ``fullmatch`` expects the column
    name, so the check is rejected as "not an IN allowlist" and migration 020
    could never be applied to a real PostgreSQL database.
    """
    value = str(expression).lower()
    value = re.sub(r"::(?:character varying|text|integer)(?:\[\])?", "", value)
    # ``CHECK`` prefixes the whole definition; it is not part of the expression.
    value = re.sub(r"^\s*check\b", "", value)
    value = re.sub(r"\(([a-z_][a-z_0-9]*)\)", r"\1", value)
    value = re.sub(r"[\s\"']", "", value)
    value = value.replace("=any(array[", "in(").replace("])", ")")
    # ``(x)`` and ``((x))`` denote the same expression; drop only balanced outer parens.
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    match = re.fullmatch(r"([a-z_][a-z_0-9]*)in\(([^()]*)\)", value)
    if match is None:
        raise ValueError("stock qualification grade check is not an IN allowlist")
    column, allowed = match.group(1), tuple(sorted(part for part in match.group(2).split(",") if part))
    if column != GRADE_COLUMN or not allowed or any(not re.fullmatch(r"[a-z_][a-z_0-9_]*", part) for part in allowed):
        raise ValueError("stock qualification grade allowlist is invalid")
    return column, allowed


def expected_grade():
    from app.models.stock_research_dataset import StockResearchDataset
    checks = {item.name: str(item.sqltext) for item in StockResearchDataset.__table__.constraints if hasattr(item, "sqltext")}
    if GRADE_CHECK not in checks:
        raise ValueError("stock research dataset model lost the qualification grade check")
    return grade_allowance(checks[GRADE_CHECK])


def capability_assessments(*, factor_fields, industry_fields, backtest_fields, coverage, blockers):
    """Resolve every per-action capability against the rules actually requested.

    Dependencies are resolved per action: a 20-day momentum study is never blocked
    for a missing gross margin or ROE, while an industry rotation always needs a
    certifiable historical industry.  A selected feature with no eligible data is
    a hard refusal, never a silently dropped column.
    """
    gaps = {str(reason).split(":", 1)[0] for reason in blockers}
    selected, industry, prices = sorted(factor_fields), sorted(industry_fields), sorted(backtest_fields)

    def eligible(field):
        return int(coverage.get(field, {}).get("eligible_observations", 0)) > 0

    shared = sorted(gaps & {"observation_only_dataset", "calendar_unverified", "historical_universe_unverified",
        "source_authorization_evidence_unverified"})
    price_gaps = [f"price_volume_fields_unavailable:{field}" for field in prices if not eligible(field)]
    execution_gaps = sorted(gaps & {"adjustment_basis_unverified", "execution_evidence_unavailable",
        "corporate_action_economic_results_unavailable", "delisting_economic_results_unavailable"})

    def assessment(name, fields, extra, industry_required):
        missing = sorted(set(shared + extra + [f"selected_fields_unavailable:{field}" for field in fields if not eligible(field)]
            + (["historical_industry_unavailable"] if industry_required and INDUSTRY_FIELD in fields else [])))
        if any(not eligible(field) for field in fields):
            scope = "none"
        elif name == "portfolio":
            scope = "backtest_ready" if derive_qualification(missing) == "backtest_ready" else "qa_only"
        else:
            scope = "research_only" if derive_qualification(missing) in QUALIFICATIONS[1:] else "qa_only"
        return {"name": name, "required_fields": sorted(fields), "coverage": {field: coverage[field] for field in sorted(fields) if field in coverage},
            "missing_reasons": missing, "allowed_scope": scope, "limitations": list(CAPABILITY_LIMITATIONS[name])}

    rotation_fields = sorted(set(selected) | set(industry))
    resolved = [assessment("analysis", selected, [], False), assessment("portfolio", prices, price_gaps + execution_gaps, False),
        assessment("ml", selected, [], False), assessment("rotation", rotation_fields, [], True)]
    return [CapabilityAssessment.model_validate(item).model_dump(mode="json") for item in resolved]


def _evidence(value, model):
    if value is None:
        return None
    return value if isinstance(value, model) else model.model_validate(value)


async def qualification_ready(session):
    """Fail-closed readiness for the graded qualification capability.

    Migration 020 must be registered with a matching checksum and its relaxed
    grade CHECK must be reflected in the live schema.  A missing version table,
    a missing version row, a checksum mismatch or a legacy constraint all refuse
    service; nothing is auto-created or re-collected here.  The identifiers are
    module constants, never request input.
    """
    try:
        bind = session.get_bind()
        if bind.dialect.name == "postgresql":
            if await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || '" + VERSION_TABLE + "')")) is None:
                return False
        elif not await session.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='" + VERSION_TABLE + "'")):
            return False
        checksum = await session.scalar(text("SELECT checksum FROM " + VERSION_TABLE + " WHERE version='" + SCHEMA_VERSION + "'"))
        if checksum != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
            return False

        def reflected(engine):
            inspector = inspect(engine)
            if not inspector.has_table(DATASET_TABLE):
                return False
            checks = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(DATASET_TABLE)}
            if GRADE_CHECK not in checks:
                return False
            return grade_allowance(checks[GRADE_CHECK]) == expected_grade()

        return bool(reflected(getattr(bind, "sync_engine", bind)))
    except (ValueError, OSError):
        return False


async def inspect_requirements(archive, reference, request: ResearchRequirements, *, universe=None, calendar=None,
        authorization=None, execution=None):
    """Grade a fixed dataset from evidence; no argument can force a higher grade.

    ``calendar``/``universe``/``authorization``/``execution`` are optional
    version-bound certification records.  Omitting one does not raise an error,
    it lowers the derived grade and names the missing prerequisite.
    """
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
        required.add(INDUSTRY_FIELD)
    factor_fields = set(required) - ({INDUSTRY_FIELD} if needs_industry else set())
    industry_fields = {INDUSTRY_FIELD} if needs_industry else set()
    backtest_fields = set(PRICE_FIELDS) | set(VOLUME_FIELDS) | {ADJUSTMENT_FIELD}
    if request.weight == "mktcap":
        backtest_fields.add("marketcap")
    if request.action in ("backtest", "rotation"):
        required |= backtest_fields
    inspected = set(required) | backtest_fields | factor_fields | industry_fields
    fields = {field: {"raw_observations": 0, "eligible_observations": 0, "source_null": 0, "excluded_domain": 0, "basis_unverified": 0, "temporal_unverified": 0} for field in sorted(inspected)}
    start, end = date.fromisoformat(manifest["warmup_start"]), date.fromisoformat(manifest["end"])
    decision = datetime.combine(end, time(15), ZoneInfo("Asia/Shanghai"))
    instruments = set(manifest["instruments"])
    pages = []
    for source, artifact in await archive.source_rows([UUID(item["source_id"]) for item in manifest["sources"]]):
        page = archive.verified_source(source, artifact)
        pages.append(page)
        for row in page.observations:
            if row.instrument_id not in instruments or (page.kind != "financial" and not start <= row.effective_date <= end):
                continue
            for field in inspected & row.values.keys():
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
    missing = [field for field in sorted(required) if fields[field]["eligible_observations"] == 0]
    certified_calendar, certified_universe = _evidence(calendar, TradingCalendar), _evidence(universe, HistoricalUniverse)
    certified_authorization, certified_execution = _evidence(authorization, SourceAuthorizationEvidence), _evidence(execution, ExecutionEvidence)
    blockers = []
    if not any(item is not None for item in (certified_calendar, certified_universe, certified_authorization)):
        blockers.append("observation_only_dataset")
    calendar_declared = bool(pages) and all(page.calendar_reference.strip() and page.calendar_reference != "unverified" for page in pages)
    if (certified_calendar is None or not certified_calendar.start <= start or not end <= certified_calendar.end
            or not calendar_declared):
        blockers.append("calendar_unverified")
    universe_members = {member.instrument_id for member in certified_universe.members} if certified_universe is not None else set()
    if not (certified_universe is not None and certified_universe.pool_id == manifest["pool_id"]
            and certified_universe.pool_version == manifest["pool_version"] and certified_universe.start <= start
            and end <= certified_universe.end and set(manifest["instruments"]) <= universe_members):
        blockers.append("historical_universe_unverified")
    authorization_declared = bool(pages) and all(page.authorization_reference.strip() and page.source_schema_reference.strip() for page in pages)
    if certified_authorization is None or not authorization_declared:
        blockers.append("source_authorization_evidence_unverified")
    adjustment_explicit = any(page.kind == "daily" for page in pages) and all(page.price_adjustment in ADJUSTMENT_BASES
        and (page.price_adjustment != "qfq" or page.adjustment_anchor is not None) for page in pages if page.kind == "daily")
    if not adjustment_explicit:
        blockers.append("adjustment_basis_unverified")
    execution_rules = certified_execution is not None and all(
        getattr(certified_execution, name) is not None and str(getattr(certified_execution, name)).strip()
        for name in EXECUTION_REFERENCES) and certified_execution.coverage_start <= start and end <= certified_execution.coverage_end
    corporate_actions = bool(execution_rules) and bool(str(certified_execution.corporate_actions_reference).strip())
    delisting_results = bool(execution_rules) and bool(str(certified_execution.delistings_reference).strip())
    if request.action in ("backtest", "rotation") and not execution_rules:
        blockers.append("execution_evidence_unavailable")
    if not corporate_actions:
        blockers.append("corporate_action_economic_results_unavailable")
    if not delisting_results:
        blockers.append("delisting_economic_results_unavailable")
    if missing:
        blockers.append("selected_fields_unavailable")
        blockers.extend(f"selected_fields_unavailable:{field}" for field in missing)
    if needs_industry:
        blockers.append("historical_industry_unavailable")
    blockers.extend(f"price_volume_fields_unavailable:{field}" for field in sorted(backtest_fields)
        if field not in required and fields[field]["eligible_observations"] == 0)
    qualification = derive_qualification(blockers)
    return {"method_version": METHOD_VERSION, "dataset": reference.model_dump(mode="json"), "qualification": qualification,
        "research_enabled": allows(qualification, "analysis"),
        "required_fields": sorted(required), "missing_fields": missing, "fields": fields, "lookback": lookback, "custom_factors": custom,
        "blocking_reasons": sorted(set(blockers)), "capabilities": capability_assessments(factor_fields=factor_fields,
            industry_fields=industry_fields, backtest_fields=backtest_fields, coverage=fields, blockers=blockers),
        "coverage_basis": "source_observations_not_trading_grid",
        "warnings": ["记录存在不代表跨日期/证券完整覆盖，未以同源一致认证真实性。", "未核实字段可得时间与所需全部依赖前，不运行IC或回测。",
            "资格只能由规则证据提升，没有管理员按钮、请求开关或清单改写可以升级。"]}
