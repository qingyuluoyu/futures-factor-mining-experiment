from datetime import date, datetime, timezone
import math

from sqlalchemy import and_, case, func, select, text

from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.schemas.stock_fundamentals import FundamentalQuery
from app.services.stock_factors.engine import ResearchDataError


MAX_SOURCE_ROWS = 50000
METHOD_VERSION = "stock-fundamentals-history-v1"
FINANCIAL_UNITS = {"roe": "percent", "gross_margin": "percent", "revenue_yoy": "percent", "netprofit_yoy": "percent", "eps": "CNY_per_share", "bps": "CNY_per_share", "netprofit": "CNY"}
VALUATION_UNITS = {"pe_ttm": "ratio", "pb": "ratio", "total_mv": "CNY_100_million", "circ_mv": "CNY_100_million", "turnover_rate": "percent"}
LIMITATIONS = [
    "旧导入记录的供应商、采集时点、授权和修订版本未逐行认证；不是PIT历史时点证据。",
    "财报按公告日期筛选且含截止当日，仅用于展示；不授权研究当日使用公告或模拟成交。",
    "固定截止日和稳定排序不等于不可变快照，后续导入或修订可能改变跨页结果。",
    "负PE/PB和其他有限负值按原值展示，不代表满足价值因子筛选；缺失保留null，不填零。",
]


def numeric_values(row, fields):
    values = {}
    for field in fields:
        value = getattr(row, field)
        if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
            raise ResearchDataError("invalid_fundamental_values", "估值或财务包含非有限或非法值，请核查来源；未转成空值", 409)
        values[field] = value
    return values


async def read_fundamentals(factory, query: FundamentalQuery, *, today: date) -> dict:
    if type(today) is not date or query.as_of > today:
        raise ResearchDataError("invalid_fundamental_date", "截止日期不得晚于上海今天", 422)
    model = StockFinancial if query.kind == "financial" else StockValuation
    units = FINANCIAL_UNITS if query.kind == "financial" else VALUATION_UNITS
    if query.kind == "financial":
        unknown = model.publish_date.is_(None)
        invalid = and_(model.publish_date.is_not(None), model.report_date > model.publish_date)
        eligible = and_(model.publish_date.is_not(None), model.report_date <= model.publish_date, model.publish_date <= query.as_of)
        later = and_(model.publish_date.is_not(None), model.report_date <= model.publish_date, model.publish_date > query.as_of)
        order = (model.report_date.desc(), model.publish_date.desc(), model.id.desc())
    else:
        unknown = invalid = False
        eligible, later = model.trade_date <= query.as_of, model.trade_date > query.as_of
        order = (model.trade_date.desc(), model.id.desc())
    async with factory() as session, session.begin():
        dialect = session.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise ResearchDataError("fundamental_backend_unsupported", "历史读取需要一致性事务支持", 503)
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ" if dialect == "postgresql" else "SERIALIZABLE"})
        if dialect == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='5s'"))
            await session.execute(text("SET LOCAL lock_timeout='2s'"))
            readonly = (await session.execute(text("SHOW transaction_read_only"))).scalar_one()
            isolation = (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
            if readonly != "on" or isolation != "repeatable read":
                raise ResearchDataError("fundamental_transaction_unsafe", "历史读取只读或隔离保护未生效", 503)
        else:
            await session.execute(text("BEGIN"))
        instrument = (await session.execute(select(StockInstrument).where(StockInstrument.code == query.code))).scalar_one_or_none()
        if instrument is None:
            raise ResearchDataError("fundamental_instrument_missing", "证券不在当前已导入目录中", 404)
        if instrument.exchange not in ("SSE", "SZSE", "BSE"):
            raise ResearchDataError("fundamental_exchange_unknown", "目录交易所未标准化，不能推断证券标识", 409)
        counts = dict((await session.execute(select(func.count().label("source_rows"),
            func.count(case((eligible, 1))).label("eligible_rows"), func.count(case((unknown, 1))).label("unknown_publication"),
            func.count(case((invalid, 1))).label("invalid_dates"), func.count(case((later, 1))).label("later_rows"))
            .where(model.code == query.code))).mappings().one())
        if counts["source_rows"] > MAX_SOURCE_ROWS:
            raise ResearchDataError("fundamental_limit_exceeded", "单证券源记录超过安全读取边界，请先核查目录和源表", 422)
        records = list((await session.execute(select(model).where(model.code == query.code, eligible)
            .order_by(*order).offset(query.offset).limit(query.limit + 1))).scalars().all())
        rows = []
        for record in records[:query.limit]:
            dates = {"report_date": record.report_date.isoformat(), "publish_date": record.publish_date.isoformat()} if query.kind == "financial" else {"trade_date": record.trade_date.isoformat()}
            rows.append({**dates, **numeric_values(record, units)})
        identity = {"code": instrument.code, "name": instrument.name, "exchange": instrument.exchange, "instrument_id": f"{instrument.code}.{instrument.exchange}"}
    return {"history_version": METHOD_VERSION, "market": "stock", "query": query.model_dump(mode="json"), "instrument": identity,
        "counts": counts, "rows": rows, "has_more": len(records) > query.limit, "units": units,
        "source": "local_import", "provenance": "legacy_unverified", "data_version": None,
        "supplier": "unknown", "license_scope": "unverified", "collected_at": None, "trading_calendar": "unverified",
        "frequency": "quarterly" if query.kind == "financial" else "daily", "financial_basis": "unverified",
        "price_adjustment": "not_applicable", "unit_status": "schema_declared_not_row_verified",
        "date_semantics": {"report_date": "report_period_end", "publish_date": "disclosure_date_inclusive_display_only"} if query.kind == "financial" else {"trade_date": "trading_date"},
        "point_in_time_certified": False, "currency": "CNY", "timezone": "Asia/Shanghai",
        "transaction": "postgresql_repeatable_read_readonly" if dialect == "postgresql" else "sqlite_explicit_select_only",
        "read_at": datetime.now(timezone.utc).isoformat(), "source_calls": 0, "model_calls": 0, "limitations": list(LIMITATIONS)}
