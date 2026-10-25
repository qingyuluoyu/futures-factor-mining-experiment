"""统一盘口面板：五档深度、日内统计与明确的数据质量。"""

from __future__ import annotations

import time
from typing import Any

from app.data.product_specs import get_product_spec

MarketField = dict[str, Any]


def _missing() -> MarketField:
    return {"value": None, "source": "missing", "quality": "missing"}


def _field(value: Any, source: str, quality: str) -> MarketField:
    return {"value": value, "source": source, "quality": quality}


def _number(value: Any, *, positive: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (-1e20 < number < 1e20):
        return None
    if positive and number <= 0:
        return None
    return number


def _direct(quote: dict[str, Any], key: str, *, positive: bool = False) -> MarketField:
    value = _number(quote.get(key), positive=positive)
    if value is None:
        return _missing()
    meta = quote.get("field_meta")
    field_meta = meta.get(key) if isinstance(meta, dict) else None
    source = (
        str(field_meta.get("source"))
        if isinstance(field_meta, dict) and field_meta.get("source")
        else str(quote.get("source") or "unknown")
    )
    quality = (
        str(field_meta.get("quality"))
        if isinstance(field_meta, dict) and field_meta.get("quality")
        else "direct"
    )
    return _field(value, source, quality)


def _levels(quote: dict[str, Any], side: str) -> list[dict[str, Any]]:
    raw = quote.get(f"{side}_levels")
    levels: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw[:5]:
            if not isinstance(item, dict):
                continue
            price = _number(item.get("price"), positive=True)
            volume = _number(item.get("volume"))
            if price is not None and volume is not None and volume >= 0:
                levels.append({"price": price, "volume": int(volume)})
    if levels:
        return levels
    price = _number(quote.get(f"{side}_price"), positive=True)
    volume = _number(quote.get(f"{side}_vol"))
    if price is None:
        return []
    return [{"price": price, "volume": max(0, int(volume or 0))}]


def _same_trading_day(
    quote: dict[str, Any],
    previous_quote: dict[str, Any] | None,
) -> bool:
    if previous_quote is None:
        return False
    current_day = str(quote.get("trade_date") or "")
    previous_day = str(previous_quote.get("trade_date") or "")
    return bool(current_day and current_day == previous_day)


def _previous_counter(
    previous_panel: dict[str, Any] | None,
    name: str,
    *,
    same_day: bool,
) -> int:
    if not same_day or not isinstance(previous_panel, dict):
        return 0
    stats = previous_panel.get("stats")
    field = stats.get(name) if isinstance(stats, dict) else None
    try:
        return max(0, int(float(field.get("value") or 0)))
    except (AttributeError, TypeError, ValueError):
        return 0


def _trade_direction(
    quote: dict[str, Any],
    previous_quote: dict[str, Any],
    previous_panel: dict[str, Any] | None,
) -> str:
    last = _number(quote.get("last_price"), positive=True) or 0.0
    bid = _number(quote.get("bid_price"), positive=True) or 0.0
    ask = _number(quote.get("ask_price"), positive=True) or 0.0
    previous_last = _number(previous_quote.get("last_price"), positive=True) or last
    if ask and last >= ask:
        return "buy"
    if bid and last <= bid:
        return "sell"
    if last > previous_last:
        return "buy"
    if last < previous_last:
        return "sell"
    if isinstance(previous_panel, dict):
        direction = str(previous_panel.get("last_direction") or "")
        if direction in ("buy", "sell"):
            return direction
    return "buy"


def build_market_panel(
    quote: dict[str, Any],
    previous_quote: dict[str, Any] | None = None,
    previous_panel: dict[str, Any] | None = None,
    *,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """生成两个页面共用的稳定盘口负载。"""
    now = float(now_ts if now_ts is not None else time.time())
    symbol = str(quote.get("symbol") or "").strip().lower()
    asks = _levels(quote, "ask")
    bids = _levels(quote, "bid")
    asof = _number(quote.get("recv_ts")) or now
    stale = now - asof > 90
    same_day = _same_trading_day(quote, previous_quote)

    current_volume: int | None = None
    direction = ""
    if same_day and previous_quote is not None:
        current_total = int(_number(quote.get("volume")) or 0)
        previous_total = int(_number(previous_quote.get("volume")) or 0)
        if current_total >= previous_total:
            current_volume = current_total - previous_total
        if current_volume and current_volume > 0:
            direction = _trade_direction(quote, previous_quote, previous_panel)
    # 单秒持仓变化（开平拆分原料）：同交易日且两侧持仓均有效才算；
    # |ΔOI| 不可能超过成交量（全部开/全部平的极值），超界视为源数据异常丢弃
    oi_delta: int | None = None
    if current_volume is not None and current_volume > 0:
        cur_position = _number(quote.get("position"))
        prev_position = _number(previous_quote.get("position")) if previous_quote else None
        if same_day and cur_position is not None and prev_position is not None:
            delta = int(round(cur_position)) - int(round(prev_position))
            if abs(delta) <= current_volume:
                oi_delta = delta
    outer = _previous_counter(previous_panel, "outer_volume", same_day=same_day)
    inner = _previous_counter(previous_panel, "inner_volume", same_day=same_day)
    if current_volume and direction == "buy":
        outer += current_volume
    elif current_volume and direction == "sell":
        inner += current_volume

    position = _number(quote.get("position"))
    pre_position = _number(quote.get("pre_open_interest"))
    daily_oi_change = (
        _field(position - pre_position, "derived", "derived")
        if position is not None and pre_position is not None
        else _missing()
    )
    high = _number(quote.get("high_price"), positive=True)
    low = _number(quote.get("low_price"), positive=True)
    pre_settlement = _number(quote.get("pre_close"), positive=True)
    amplitude = (
        _field((high - low) / pre_settlement * 100, "derived", "derived")
        if high is not None and low is not None and pre_settlement is not None
        else _missing()
    )

    volume = int(_number(quote.get("volume")) or 0)
    multiplier = int(get_product_spec(symbol).get("multiplier") or 1)
    turnover_direct = _number(quote.get("turnover"), positive=True)
    if turnover_direct is not None:
        turnover = _direct(quote, "turnover", positive=True)
        average_price = (
            _field(turnover_direct / volume / multiplier, "derived", "derived")
            if volume > 0 and multiplier > 0
            else _missing()
        )
    else:
        last = _number(quote.get("last_price"), positive=True)
        if last is not None and volume > 0 and multiplier > 0:
            turnover = _field(last * volume * multiplier, "derived", "estimated")
            average_price = _field(last, "derived", "estimated")
        else:
            turnover = _missing()
            average_price = _missing()

    stats = {
        "open_price": _direct(quote, "open_price", positive=True),
        "upper_limit_price": _direct(quote, "upper_limit_price", positive=True),
        "lower_limit_price": _direct(quote, "lower_limit_price", positive=True),
        "high_price": _direct(quote, "high_price", positive=True),
        "low_price": _direct(quote, "low_price", positive=True),
        "average_price": average_price,
        "volume_ratio": _missing(),
        "pre_settlement_price": _direct(quote, "pre_close", positive=True),
        "settlement_price": _direct(quote, "settlement_price", positive=True),
        "basis": _missing(),
        "daily_oi_change": daily_oi_change,
        "pre_open_interest": _direct(quote, "pre_open_interest"),
        "open_interest": _direct(quote, "position"),
        "amplitude": amplitude,
        "current_volume": (
            _field(current_volume, "derived", "estimated")
            if current_volume is not None
            else _missing()
        ),
        "oi_delta": (
            _field(oi_delta, "derived", "estimated")
            if oi_delta is not None
            else _missing()
        ),
        "volume": _direct(quote, "volume"),
        "turnover": turnover,
        "outer_volume": _field(outer, "derived", "estimated"),
        "inner_volume": _field(inner, "derived", "estimated"),
        "spot": _missing(),
    }
    return {
        "symbol": symbol,
        "source": str(quote.get("source") or "unknown"),
        "asof": asof,
        "stale": stale,
        "trade_date": str(quote.get("trade_date") or ""),
        "last_direction": direction or (
            str(previous_panel.get("last_direction") or "")
            if same_day and isinstance(previous_panel, dict)
            else ""
        ),
        "asks": asks,
        "bids": bids,
        "stats": stats,
    }

