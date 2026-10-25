"""CTP DepthMarketData → 内部 quote（纯函数）"""

from __future__ import annotations

from typing import Any

from app.connectors.ctp.instrument_map import (
    build_instrument_to_canonical,
    resolve_canonical_symbol,
)
from app.data.contracts import ALL_CONTRACTS
from app.data.quote_schema import normalize_quote_fields

# 模块级别名表（启动时构建一次）
_ALIAS_TO_CANON: dict[str, str] = build_instrument_to_canonical(ALL_CONTRACTS)


def _contract_index() -> dict[str, dict[str, str]]:
    """symbol 小写 → 合约元数据"""
    return {c["symbol"].lower(): c for c in ALL_CONTRACTS}


def instrument_to_symbol(instrument_id: str) -> str:
    """CTP InstrumentID → 内部 symbol（仅同月别名，不跨月）"""
    return resolve_canonical_symbol(instrument_id, _ALIAS_TO_CANON)


def _safe_float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        # CTP 常用 double 最大值表示无效价
        f = float(value)
        if f > 1e20 or f < -1e20:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        f = float(value)
        if f > 1e20 or f < -1e20:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_optional_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if number > 1e20 or number < -1e20:
            return None
        return int(number)
    except (TypeError, ValueError):
        return None


def _depth_levels(tick: dict[str, Any], side: str) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    prefix = "Bid" if side == "bid" else "Ask"
    snake = "bid" if side == "bid" else "ask"
    for level in range(1, 6):
        price = _safe_optional_float(
            tick.get(
                f"{prefix}Price{level}",
                tick.get(f"{snake}_price_{level}"),
            )
        )
        volume = _safe_int(
            tick.get(
                f"{prefix}Volume{level}",
                tick.get(f"{snake}_volume_{level}"),
            )
        )
        if price is None or price <= 0:
            continue
        levels.append({"price": price, "volume": max(0, volume)})
    return levels


def depth_to_quote(
    tick: dict[str, Any],
    contract_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """将 CTP 深度行情 dict 转为内部 quote

    接受字段名兼容：原生 Ctp 属性名（LastPrice）或 snake 别名。
    """
    idx = contract_map if contract_map is not None else _contract_index()
    inst = str(
        tick.get("InstrumentID")
        or tick.get("instrument_id")
        or tick.get("symbol")
        or ""
    )
    symbol = instrument_to_symbol(inst)
    if not symbol:
        return None
    # 必须是合约表内精确合约：禁止 cu2609 串写到 cu2607
    meta = idx.get(symbol)
    if meta is None:
        return None

    last = _safe_float(tick.get("LastPrice", tick.get("last_price")), 0.0)
    if last <= 0:
        return None
    pre = _safe_float(
        tick.get("PreSettlementPrice", tick.get("pre_close")),
        last,
    )
    open_p = _safe_float(tick.get("OpenPrice", tick.get("open_price")), last)
    high = _safe_float(tick.get("HighestPrice", tick.get("high_price")), last)
    low = _safe_float(tick.get("LowestPrice", tick.get("low_price")), last)
    bid = _safe_float(tick.get("BidPrice1", tick.get("bid_price")), last)
    ask = _safe_float(tick.get("AskPrice1", tick.get("ask_price")), last)
    bid_vol = _safe_int(tick.get("BidVolume1", tick.get("bid_vol")))
    ask_vol = _safe_int(tick.get("AskVolume1", tick.get("ask_vol")))
    bid_levels = _depth_levels(tick, "bid")
    ask_levels = _depth_levels(tick, "ask")
    volume = _safe_int(tick.get("Volume", tick.get("volume")))
    position = _safe_int(tick.get("OpenInterest", tick.get("position")))
    pre_open_interest = _safe_optional_int(
        tick.get("PreOpenInterest", tick.get("pre_open_interest"))
    )
    turnover = _safe_optional_float(tick.get("Turnover", tick.get("turnover")))
    settlement_price = _safe_optional_float(
        tick.get("SettlementPrice", tick.get("settlement_price"))
    )
    upper_limit_price = _safe_optional_float(
        tick.get("UpperLimitPrice", tick.get("upper_limit_price"))
    )
    lower_limit_price = _safe_optional_float(
        tick.get("LowerLimitPrice", tick.get("lower_limit_price"))
    )
    avg = _safe_float(tick.get("AveragePrice", tick.get("avg_price")), 0.0)
    # CTP AveragePrice 常为「成交额/量」未除合约乘数，会远大于现价，不能当地均
    if avg <= 0 or avg > last * 5 or avg < last * 0.2:
        avg = 0.0

    update_time = str(tick.get("UpdateTime", tick.get("tick_time")) or "")
    action_day = str(tick.get("ActionDay", tick.get("trade_date")) or "")
    update_millisec = _safe_int(
        tick.get("UpdateMillisec", tick.get("update_millisec"))
    )
    # UpdateTime 常为 HH:MM:SS → 压成 HHmmss 兼容现有 UI
    tick_time = update_time.replace(":", "") if ":" in update_time else update_time

    change = last - pre if pre else 0.0
    change_pct = (change / pre) * 100 if pre else 0.0

    raw = {
        "symbol": meta.get("symbol", symbol),
        "name": meta.get("name", symbol),
        "exchange": meta.get("exchange", ""),
        "last_price": last,
        "change": change,
        "change_pct": change_pct,
        "open_price": open_p,
        "high_price": high,
        "low_price": low,
        "pre_close": pre,
        "bid_price": bid if bid > 0 else last,
        "ask_price": ask if ask > 0 else last,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "bid_levels": bid_levels,
        "ask_levels": ask_levels,
        "volume": volume,
        "position": position,
        "pre_open_interest": pre_open_interest,
        "turnover": turnover,
        "settlement_price": settlement_price,
        "upper_limit_price": upper_limit_price,
        "lower_limit_price": lower_limit_price,
        "avg_price": avg,
        "tick_time": tick_time,
        "update_millisec": update_millisec,
        "trade_date": action_day,
    }
    return normalize_quote_fields(raw)
