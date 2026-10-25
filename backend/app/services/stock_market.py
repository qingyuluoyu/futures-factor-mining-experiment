import math
from collections import defaultdict
from datetime import date, datetime
from statistics import fmean
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kline_history import KlineHistory
from app.models.stock import StockFinancial, StockInstrument, StockValuation


class StockMarketError(ValueError):
    pass


def finite_number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def valid_bar(row: dict | None) -> bool:
    if row is None:
        return False
    prices = [finite_number(row.get(field)) for field in ("open", "high", "low", "close")]
    if any(value is None or value <= 0 for value in prices):
        return False
    opening, high, low, close = prices
    volume = finite_number(row.get("volume"))
    return low <= min(opening, close) <= max(opening, close) <= high and volume is not None and volume >= 0


def instrument_id(code: str, exchange: str) -> str:
    suffix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(exchange)
    if suffix is None:
        raise StockMarketError("证券交易所缺失或不受支持")
    return f"{code}.{suffix}"


def build_market_snapshot(instruments: list[dict], bars: list[dict], as_of: date | None, previous: date | None, *, reference_closes: dict | None = None) -> dict:
    indexed = {}
    for row in bars:
        key = row["code"], row["trade_date"]
        if key in indexed:
            raise StockMarketError("市场快照包含重复日线")
        indexed[key] = row
    items = []
    grouped = defaultdict(list)
    for instrument in instruments:
        code = instrument["code"]
        latest = indexed.get((code, as_of))
        before = indexed.get((code, previous))
        ready = valid_bar(latest)
        reference = finite_number(reference_closes.get(code)) if reference_closes is not None else float(before["close"]) if valid_bar(before) else None
        comparable = ready and reference is not None and reference > 0
        close = float(latest["close"]) if ready else None
        change = close - reference if comparable else None
        change_pct = change / reference * 100 if comparable else None
        last_trade_date = instrument.get("last_trade_date") or (as_of if latest else previous if before else None)
        row = {
            "code": code, "name": instrument["name"], "exchange": instrument["exchange"],
            "instrument_id": instrument_id(code, instrument["exchange"]), "industry": instrument.get("industry") or "未分类",
            "close": close, "change": round(change, 6) if change is not None else None,
            "change_pct": round(change_pct, 6) if change_pct is not None else None,
            "volume": float(latest["volume"]) if ready else None,
            "trade_date": as_of.isoformat() if ready else None, "data_available": ready,
            "last_trade_date": last_trade_date.isoformat() if last_trade_date else None,
            "data_status": "ready" if comparable else "missing_previous" if ready else "invalid_quote" if latest else "stale_quote" if instrument.get("last_trade_date") or before else "no_daily",
        }
        items.append(row)
        grouped[row["industry"]].append(row)
    boards = []
    for industry, members in grouped.items():
        comparable = [row for row in members if row["change_pct"] is not None]
        leader = max(comparable, key=lambda row: (row["change_pct"], row["code"]), default=None)
        boards.append({
            "industry": industry, "count": len(members), "quoted_count": sum(row["data_available"] for row in members),
            "comparable_count": len(comparable), "advancing": sum(row["change_pct"] > 0 for row in comparable),
            "declining": sum(row["change_pct"] < 0 for row in comparable), "flat": sum(row["change_pct"] == 0 for row in comparable),
            "change_pct": round(fmean(row["change_pct"] for row in comparable), 6) if comparable else None,
            "leader": {field: leader[field] for field in ("code", "name", "change_pct")} if leader else None,
        })
    boards.sort(key=lambda row: (-row["count"], row["industry"]))
    return {
        "as_of": as_of.isoformat() if as_of else None, "previous_date": previous.isoformat() if previous else None,
        "classification": "stock_basic行业（当前分类，非历史行业成分）", "source": "本地授权导入库",
        "price_adjustment": "qfq", "volume_unit": "lot_100_shares", "method_version": "stock-market-snapshot-v1",
        "coverage": {"instruments": len(items), "quoted": sum(row["data_available"] for row in items), "comparable": sum(row["change_pct"] is not None for row in items)},
        "boards": boards, "items": items,
    }


def filter_members(items: list[dict], *, industry: str = "", keyword: str = "", sort: str = "code", direction: str = "asc", data_only: bool = False) -> list[dict]:
    keyword = keyword.strip().casefold()
    rows = [row for row in items if (not industry or row["industry"] == industry) and (not keyword or keyword in row["code"].casefold() or keyword in row["name"].casefold()) and (not data_only or row["data_available"])]
    if sort == "change_pct":
        sign = -1 if direction == "desc" else 1
        return sorted(rows, key=lambda row: (row["change_pct"] is None, sign * (row["change_pct"] or 0), row["code"]))
    return sorted(rows, key=lambda row: (row[sort], row["code"]), reverse=direction == "desc")


async def load_market_snapshot(session: AsyncSession, *, prefer_eod: bool = False) -> dict:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if prefer_eod:
        from app.services.stock_eod import latest_eod, market_from_eod

        latest = await latest_eod(session, today)
        if latest is not None:
            instruments = (await session.execute(select(StockInstrument.code, StockInstrument.name, StockInstrument.exchange, StockInstrument.industry).order_by(StockInstrument.code).limit(10001))).mappings().all()
            if len(instruments) > 10000:
                raise StockMarketError("股票目录超过10000条安全边界")
            try:
                return market_from_eod(latest.snapshot_json, [dict(row) for row in instruments])
            except (ValueError, KeyError, TypeError):
                raise StockMarketError("盘后行情快照校验失败，请凭请求编号核查") from None
    dates = (await session.execute(select(KlineHistory.trade_date).where(KlineHistory.market_type == "stock", KlineHistory.trade_date <= today).distinct().order_by(KlineHistory.trade_date.desc()).limit(2))).scalars().all()
    instruments = (await session.execute(select(StockInstrument.code, StockInstrument.name, StockInstrument.exchange, StockInstrument.industry).order_by(StockInstrument.code).limit(10001))).mappings().all()
    if len(instruments) > 10000:
        raise StockMarketError("股票目录超过10000条安全边界，需调整数据分区后使用")
    bars = []
    if dates:
        bars = (await session.execute(select(KlineHistory.code, KlineHistory.trade_date, KlineHistory.open, KlineHistory.high, KlineHistory.low, KlineHistory.close, KlineHistory.volume).where(KlineHistory.market_type == "stock", KlineHistory.trade_date.in_(dates)).limit(20001))).mappings().all()
        if len(bars) > 20000:
            raise StockMarketError("市场快照超过20000条安全边界")
    latest_dates = dict((await session.execute(select(KlineHistory.code, func.max(KlineHistory.trade_date)).where(KlineHistory.market_type == "stock", KlineHistory.trade_date <= today).group_by(KlineHistory.code).limit(10001))).all())
    if len(latest_dates) > 10000:
        raise StockMarketError("行情证券超过10000条安全边界")
    return build_market_snapshot([{**dict(row), "last_trade_date": latest_dates.get(row["code"])} for row in instruments], [dict(row) for row in bars], dates[0] if dates else None, dates[1] if len(dates) > 1 else None)


async def stock_details(session: AsyncSession, code: str) -> dict | None:
    instrument = await session.scalar(select(StockInstrument).where(StockInstrument.code == code))
    if instrument is None:
        return None
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    coverage = (await session.execute(select(func.min(KlineHistory.trade_date), func.max(KlineHistory.trade_date), func.count()).where(KlineHistory.market_type == "stock", KlineHistory.code == code, KlineHistory.trade_date <= today))).one()
    valuation = await session.scalar(select(StockValuation).where(StockValuation.code == code, StockValuation.trade_date <= today).order_by(StockValuation.trade_date.desc()).limit(1))
    financials = (await session.execute(select(StockFinancial).where(StockFinancial.code == code, StockFinancial.publish_date <= today, StockFinancial.report_date <= today).order_by(StockFinancial.report_date.desc()).limit(8))).scalars().all()
    return {
        "instrument": {"code": code, "instrument_id": instrument_id(code, instrument.exchange), "name": instrument.name, "exchange": instrument.exchange, "industry": instrument.industry or "未分类", "list_date": instrument.list_date.isoformat() if instrument.list_date else None},
        "as_of": today.isoformat(), "coverage": {"first_date": coverage[0].isoformat() if coverage[0] else None, "last_date": coverage[1].isoformat() if coverage[1] else None, "daily_rows": coverage[2]},
        "valuation": {"trade_date": valuation.trade_date.isoformat(), **{field: finite_number(getattr(valuation, field)) for field in ("pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate")}} if valuation else None,
        "financials": [{"report_date": row.report_date.isoformat(), "publish_date": row.publish_date.isoformat(), **{field: finite_number(getattr(row, field)) for field in ("roe", "gross_margin", "revenue_yoy", "netprofit_yoy", "eps", "bps")}} for row in financials],
        "limitations": ["当前分类不代表历史成分", "目录无行情不等于停牌，未覆盖证券不显示默认价格", "财务仅展示公告日期不晚于当前日期的数据；未做历史修订时点认证"],
    }
