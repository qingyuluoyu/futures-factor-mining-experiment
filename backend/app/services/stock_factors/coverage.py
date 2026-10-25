from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation
from app.services.stock_factors.engine import ResearchDataError
from app.services.stock_factors.snapshots import MAX_FINANCIAL_ROWS, MAX_INSTRUMENTS, MAX_PANEL_CELLS, MAX_PRICE_ROWS, MAX_VALUATION_ROWS
from app.services.stock_factors.dependencies import resolve_dependencies


def validate_period(start_date, end_date, today):
    if any(value is not None and type(value) is not date for value in (start_date, end_date)):
        raise ResearchDataError("invalid_date_range", "覆盖日期必须为有效日期", 422)
    if (start_date and start_date > today) or (end_date and end_date > today):
        raise ResearchDataError("invalid_date_range", "覆盖结束日期不得晚于上海今天", 422)
    if start_date and end_date and (start_date > end_date or (end_date - start_date).days > 3660):
        raise ResearchDataError("invalid_date_range", "开始不得晚于结束，覆盖区间不得超过3660天", 422)


async def get_research_coverage(session_factory, start_date: date | None = None, end_date: date | None = None, *, today: date | None = None, factor_ids=None, mktcap_neutral=False, weight="equal") -> dict:
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    validate_period(start_date, end_date, today)
    plan = resolve_dependencies(factor_ids, mktcap_neutral=mktcap_neutral, weight=weight)
    selected = {name: 0 for name in ("securities", "observed_dates", "price_rows", "missing_price_cells", "valuation_rows", "financial_rows", "panel_cells")}
    price_filter = (KlineHistory.market_type == "stock", KlineHistory.trade_date <= today)
    async with session_factory() as session:
        dialect = session.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise ResearchDataError("snapshot_backend_unsupported", "覆盖预检需要一致性数据库事务", 503)
        async with session.begin():
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ" if dialect == "postgresql" else "SERIALIZABLE"})
            if dialect == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            else:
                await session.execute(text("BEGIN"))
            catalog_count = (await session.execute(select(func.count()).select_from(StockInstrument))).scalar_one()
            available_row = (await session.execute(select(
                func.min(KlineHistory.trade_date), func.max(KlineHistory.trade_date),
                func.count(func.distinct(KlineHistory.code)), func.count(), func.count(func.distinct(KlineHistory.trade_date)),
            ).where(*price_filter))).one()
            available = dict(zip(("first_trade_date", "last_trade_date", "securities", "price_rows", "observed_dates"), available_row))
            recent_dates = (await session.execute(select(KlineHistory.trade_date).where(*price_filter, KlineHistory.trade_date <= (end_date or today)).distinct().order_by(KlineHistory.trade_date.desc()).limit(61))).scalars().all()
            recommended = {"start_date": recent_dates[-1], "end_date": recent_dates[0]} if recent_dates else None
            if start_date is not None or end_date is not None:
                requested = {"start_date": start_date or (recent_dates[-1] if recent_dates else end_date), "end_date": end_date or today}
            else:
                requested = recommended
            if requested is not None:
                start, end = requested["start_date"], requested["end_date"]
                validate_period(start, end, today)
                interval = (*price_filter, KlineHistory.trade_date >= start, KlineHistory.trade_date <= end)
                codes = select(KlineHistory.code).where(*interval).distinct()
                price_rows, securities, observed_dates = (await session.execute(select(
                    func.count(), func.count(func.distinct(KlineHistory.code)), func.count(func.distinct(KlineHistory.trade_date)),
                ).where(*interval))).one()
                valuation_rows, valuation_codes, valuation_dates = (await session.execute(select(
                    func.count(), func.count(func.distinct(StockValuation.code)), func.count(func.distinct(StockValuation.trade_date)),
                ).where(StockValuation.code.in_(codes), StockValuation.trade_date >= start, StockValuation.trade_date <= end))).one() if plan.valuation_fields else (0, 0, 0)
                financial_rows = (await session.execute(select(func.count()).select_from(StockFinancial).where(
                    StockFinancial.code.in_(codes), StockFinancial.publish_date <= end, StockFinancial.report_date <= end,
                ))).scalar_one() if plan.financial_fields else 0
                price_cells = securities * observed_dates
                selected.update(securities=securities, observed_dates=observed_dates, price_rows=price_rows,
                    missing_price_cells=price_cells - price_rows, valuation_rows=valuation_rows, financial_rows=financial_rows,
                    panel_cells=plan.panel_cells(price_cells, valuation_codes * valuation_dates, bool(financial_rows)))
    limits = {"max_panel_cells": MAX_PANEL_CELLS, "max_price_rows": MAX_PRICE_ROWS, "max_valuation_rows": MAX_VALUATION_ROWS, "max_financial_rows": MAX_FINANCIAL_ROWS, "max_instruments": MAX_INSTRUMENTS}
    over_limit = any(selected[field] > limits[limit] for field, limit in (("panel_cells", "max_panel_cells"), ("price_rows", "max_price_rows"), ("valuation_rows", "max_valuation_rows"), ("financial_rows", "max_financial_rows"), ("securities", "max_instruments")))
    warnings = ["仅统计本地已有数据，不证明交易日历完整、价量合法、授权或历史时点已认证；within_limits不保证研究成功。", "目录数量不等于有价证券数量；前复权研究不是未复权日线撮合，当前分类和财报修订未归档。"]
    if selected["missing_price_cells"]:
        warnings.append(f"所选区间有{selected['missing_price_cells']}个证券×观测日缺价，持仓估值可能拒绝计算；未填充或删除这些缺价。")
    if selected["observed_dates"] < 42:
        warnings.append("观测日少于42，20日动量和20日预测周期可能没有足够样本；短窗不代表策略有效。")
    if over_limit:
        warnings.append("所选区间超过研究快照安全上限，请缩短区间，不会静默截断数据。")
    status = "no_data" if not selected["price_rows"] else "over_limit" if over_limit else "within_limits"
    return {
        "coverage_version": "stock-research-coverage-v2" if plan.selective else "stock-research-coverage-v1", "market": "stock", "frequency": "daily",
        **({"dependencies": plan.manifest()} if plan.selective else {}),
        "adjustment": "forward_adjusted_unversioned", "timezone": "Asia/Shanghai", "catalog_count": catalog_count,
        "available": {name: value.isoformat() if isinstance(value, date) else value for name, value in available.items()},
        "recommended": {name: value.isoformat() for name, value in recommended.items()} if recommended else None,
        "requested": {name: value.isoformat() for name, value in requested.items()} if requested else None,
        "selected": selected, "limits": limits, "status": status, "warnings": warnings,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
