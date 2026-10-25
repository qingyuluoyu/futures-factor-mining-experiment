import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.kline_keys import history_kline_key


class InvalidMarketCache(ValueError):
    pass


def valid_price(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def valid_quote(quote):
    if not valid_price(quote.get("last_price")):
        return False
    nonnegative = {"volume", "position", "open_price", "high_price", "low_price", "pre_close", "bid_price", "ask_price"}
    for field in nonnegative | {"change", "change_pct"}:
        value = quote.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (field in nonnegative and value < 0):
            return False
    return True


def compatible_virtual_quote(quote):
    source = str(quote.get("source") or "").lower()
    return (source == "openctp" or source.startswith("ctp:openctp")) and "pulse" not in source


def validate_virtual_bar(row, period):
    if not isinstance(row, dict):
        raise InvalidMarketCache("虚拟K线记录不是对象")
    try:
        clock = datetime.fromisoformat(row["time"])
        if clock.tzinfo is not None:
            clock = clock.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    except (KeyError, ValueError, TypeError):
        raise InvalidMarketCache("虚拟K线时间缺失或无法解析") from None
    if clock > datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None):
        raise InvalidMarketCache("虚拟K线时间晚于当前时间")
    values = [row.get(field) for field in ("open", "high", "low", "close")]
    if not all(valid_price(value) for value in values):
        raise InvalidMarketCache("虚拟K线OHLC缺失或不是有限正数")
    opening, high, low, close = values
    if high < max(opening, close) or low > min(opening, close) or high < low:
        raise InvalidMarketCache("虚拟K线OHLC范围冲突")
    volume = row.get("volume")
    if volume is not None and (isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0):
        raise InvalidMarketCache("虚拟K线成交量无效")
    return {**row, "time": clock.date().isoformat() if period == "1d" else clock.isoformat(sep=" ", timespec="seconds")}


async def observed_virtual_history(redis_conn, symbol, period):
    if redis_conn is None:
        return [], 0
    raw = await redis_conn.get(history_kline_key("virtual", symbol, period))
    if raw is None:
        return [], 0
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        raise InvalidMarketCache("虚拟历史缓存无法解析") from None
    if not isinstance(rows, list) or len(rows) > 20000:
        raise InvalidMarketCache("虚拟历史缓存类型或数量超限")
    observed = {}
    excluded = 0
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidMarketCache("虚拟历史缓存含非对象记录")
        if row.get("source") != "virtual":
            excluded += 1
            continue
        bar = validate_virtual_bar(row, period)
        key = bar["time"]
        if key in observed and observed[key] != bar:
            raise InvalidMarketCache("虚拟历史同一时间存在冲突K线")
        observed[key] = bar
    return [observed[key] for key in sorted(observed)], excluded
