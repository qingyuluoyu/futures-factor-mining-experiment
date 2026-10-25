from datetime import date, datetime, timezone

from sqlalchemy import String, and_, case, cast, func, select, text

from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockValuation
from app.schemas.stock_research_inputs import ResearchInputQuery
from app.services.stock_factors.catalog import FACTOR_BY_ID
from app.services.stock_factors.dependencies import resolve_dependencies
from app.services.stock_factors.engine import ResearchDataError
from app.services.stock_factors.snapshots import MAX_FINANCIAL_ROWS, MAX_INSTRUMENTS, MAX_PRICE_ROWS, MAX_VALUATION_ROWS


METHOD_VERSION = "stock-research-inputs-v1"
UNITS = {"pe_ttm": "ratio", "pb": "ratio", "turnover_rate": "percent", "total_mv": "100_million_CNY",
    "roe": "percent", "gross_margin": "percent", "revenue_yoy": "percent", "netprofit_yoy": "percent"}


def finite(column):
    return and_(column.is_not(None), ~func.lower(cast(column, String)).in_(("nan", "infinity", "-infinity", "inf", "-inf")))


def field_aggregates(model, fields):
    columns = [func.count().label("source_rows")]
    for name in fields:
        column = getattr(model, name)
        usable = and_(finite(column), column > 0) if name in ("pe_ttm", "pb", "total_mv") else finite(column)
        columns.extend((func.count(column).label(f"{name}_present"),
            func.count(case((finite(column), 1))).label(f"{name}_finite"), func.count(case((usable, 1))).label(f"{name}_usable")))
    return columns


def field_summary(row, fields, source):
    result = []
    for name in fields:
        rows, present, available, usable = (int(row[key]) for key in ("source_rows", f"{name}_present", f"{name}_finite", f"{name}_usable"))
        result.append({"field": name, "source": source, "unit": UNITS[name], "source_rows": rows, "finite_values": available,
            "usable_values": usable, "missing_values": rows - present, "nonfinite_values": present - available,
            "excluded_nonpositive": available - usable, "rule": "finite_strictly_positive" if name in ("pe_ttm", "pb", "total_mv") else "finite"})
    return result


async def read_research_inputs(factory, query: ResearchInputQuery, *, today: date) -> dict:
    if type(today) is not date or query.end_date > today:
        raise ResearchDataError("invalid_date_range", "输入检查结束日不得晚于上海今天", 422)
    plan = resolve_dependencies(query.factor_ids, mktcap_neutral=query.mktcap_neutral, weight=query.weight)
    interval = (KlineHistory.market_type == "stock", KlineHistory.trade_date >= query.start_date, KlineHistory.trade_date <= query.end_date)
    code = KlineHistory.code
    stripped = code
    for digit in "0123456789":
        stripped = func.replace(stripped, digit, "")
    valid_price = and_(func.length(code) == 6, stripped == "", finite(KlineHistory.close), KlineHistory.close > 0)
    fields = []
    async with factory() as session, session.begin():
        dialect = session.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise ResearchDataError("input_backend_unsupported", "输入检查需要支持一致性事务的数据库", 503)
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ" if dialect == "postgresql" else "SERIALIZABLE"})
        if dialect == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='5s'"))
            await session.execute(text("SET LOCAL lock_timeout='2s'"))
            readonly = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
            isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            if readonly != "on" or isolation != "repeatable read":
                raise ResearchDataError("input_transaction_unsafe", "输入检查只读或隔离保护未生效", 503)
        else:
            await session.execute(text("BEGIN"))
        prices = dict((await session.execute(select(func.count().label("rows"), func.count(case((valid_price, 1))).label("valid_close_rows"),
            func.count(func.distinct(code)).label("securities"), func.count(func.distinct(KlineHistory.trade_date)).label("observed_dates"))
            .where(*interval))).mappings().one())
        if prices["rows"] > MAX_PRICE_ROWS or prices["securities"] > MAX_INSTRUMENTS:
            raise ResearchDataError("input_limit_exceeded", "输入检查价格或证券数超过安全边界，请缩短区间", 422)
        duplicates = select(code, KlineHistory.trade_date).where(*interval).group_by(code, KlineHistory.trade_date).having(func.count() > 1).subquery()
        prices["duplicate_groups"] = (await session.execute(select(func.count()).select_from(duplicates))).scalar_one()
        prices["invalid_rows"] = prices["rows"] - prices["valid_close_rows"]
        codes = select(code).where(*interval, valid_price).distinct()
        if plan.valuation_fields:
            corresponding_price = select(KlineHistory.id).where(*interval, valid_price,
                code == StockValuation.code, KlineHistory.trade_date == StockValuation.trade_date).exists()
            row = (await session.execute(select(*field_aggregates(StockValuation, plan.valuation_fields)).where(corresponding_price))).mappings().one()
            if row["source_rows"] > MAX_VALUATION_ROWS:
                raise ResearchDataError("input_limit_exceeded", "输入检查估值行超过安全边界", 422)
            fields.extend(field_summary(row, plan.valuation_fields, "valuation"))
        if plan.financial_fields:
            row = (await session.execute(select(*field_aggregates(StockFinancial, plan.financial_fields)).where(
                StockFinancial.code.in_(codes), StockFinancial.report_date <= StockFinancial.publish_date,
                StockFinancial.publish_date < query.end_date))).mappings().one()
            if row["source_rows"] > MAX_FINANCIAL_ROWS:
                raise ResearchDataError("input_limit_exceeded", "输入检查财报行超过安全边界", 422)
            fields.extend(field_summary(row, plan.financial_fields, "financial"))
    by_field = {item["field"]: item for item in fields}
    factors = []
    for factor_id in query.factor_ids:
        spec = FACTOR_BY_ID[factor_id]
        required = [] if spec.source == "kline" else ["turnover_rate" if factor_id == "turnover_20d" else factor_id]
        minimum = 21 if factor_id in ("ret_20d", "vol_20d") else 20 if factor_id == "turnover_20d" else 1
        reasons = []
        if prices["valid_close_rows"] == 0:
            reasons.append("no_valid_stock_prices")
        if prices["observed_dates"] < minimum:
            reasons.append("insufficient_observed_dates")
        if any(by_field[name]["usable_values"] == 0 for name in required):
            reasons.append("no_usable_field_values")
        factors.append({"factor_id": factor_id, "label": spec.label, "required_fields": required, "minimum_observed_dates": minimum,
            "status": "missing_inputs" if reasons else "potential_inputs", "reasons": reasons})
    auxiliary_missing = [name for name in plan.valuation_fields if name == "total_mv" and by_field[name]["usable_values"] == 0]
    status = "invalid_inputs" if prices["invalid_rows"] or prices["duplicate_groups"] else "missing_inputs" if auxiliary_missing or any(item["reasons"] for item in factors) else "potential_inputs"
    return {"inputs_version": METHOD_VERSION, "market": "stock", "query": query.model_dump(mode="json"), "status": status,
        "prices": prices, "fields": fields, "factors": factors, "auxiliary_missing": auxiliary_missing,
        "adjustment": "forward_adjusted_unversioned", "timezone": "Asia/Shanghai", "frequency": "daily",
        "point_in_time_certified": False, "source_calls": 0, "model_calls": 0, "read_at": datetime.now(timezone.utc).isoformat(),
        "transaction": "postgresql_repeatable_read_readonly" if dialect == "postgresql" else "sqlite_explicit_select_only",
        "limitations": ["只检查候选源字段和必要观测日，不重算因子；potential_inputs不保证研究成功或策略有效。",
            "财报仅统计报告期不晚于披露日且披露早于结束日的候选值，未重放报告frontier；存在修订/PIT及历史分类局限。",
            "中性化、连续窗口、horizon/groups、交易日历和持仓缺价仍由实际研究验证；不填缺价/因子，不下载来源。"]}
