"""SimNow 主源与新浪逐字段兜底（纯函数）。"""

from __future__ import annotations

import time
from typing import Any

from app.data.quote_schema import normalize_quote_fields

_PRICE_FIELDS = {
    "last_price",
    "open_price",
    "high_price",
    "low_price",
    "pre_close",
    "bid_price",
    "ask_price",
    "avg_price",
    "settlement_price",
    "upper_limit_price",
    "lower_limit_price",
}
_COUNT_FIELDS = {
    "bid_vol",
    "ask_vol",
    "volume",
    "position",
    "pre_open_interest",
    "update_millisec",
    "turnover",
}
_LEVEL_FIELDS = {"bid_levels", "ask_levels"}
_TEXT_FIELDS = {
    "name",
    "exchange",
    "tick_time",
    "trade_date",
}
_MERGE_FIELDS = (
    "name",
    "exchange",
    "last_price",
    "open_price",
    "high_price",
    "low_price",
    "pre_close",
    "bid_price",
    "ask_price",
    "bid_vol",
    "ask_vol",
    "bid_levels",
    "ask_levels",
    "volume",
    "position",
    "pre_open_interest",
    "turnover",
    "avg_price",
    "settlement_price",
    "upper_limit_price",
    "lower_limit_price",
    "tick_time",
    "update_millisec",
    "trade_date",
    "decimal_places",
)


def _valid_number(value: Any, *, positive: bool) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not (-1e20 < number < 1e20):
        return False
    return number > 0 if positive else number >= 0


def _valid_field(field: str, value: Any) -> bool:
    if field in _PRICE_FIELDS:
        return _valid_number(value, positive=True)
    if field in _COUNT_FIELDS or field == "decimal_places":
        return _valid_number(value, positive=False)
    if field in _LEVEL_FIELDS:
        return isinstance(value, list) and bool(value)
    if field in _TEXT_FIELDS:
        return bool(str(value or "").strip())
    return value is not None


def _simnow_is_fresh(
    simnow: dict[str, Any] | None,
    sina: dict[str, Any] | None,
    *,
    now_ts: float,
    stale_after: float,
) -> bool:
    if not simnow or not _valid_field("last_price", simnow.get("last_price")):
        return False
    sim_day = str(simnow.get("trade_date") or "")
    sina_day = str((sina or {}).get("trade_date") or "")
    if sim_day and sina_day and sim_day < sina_day:
        return False
    try:
        recv_ts = float(simnow.get("recv_ts") or 0)
    except (TypeError, ValueError):
        return False
    return recv_ts > 0 and 0 <= now_ts - recv_ts <= stale_after


def merge_live_quote(
    simnow: dict[str, Any] | None,
    sina: dict[str, Any] | None,
    eastmoney: dict[str, Any] | None = None,  # 已弃用（东财兜底移除），保留位置参数兼容
    *,
    now_ts: float | None = None,
    stale_after: float = 90.0,
    primary_source: str = "simnow",
) -> dict[str, Any] | None:
    """按字段合并同一合约；优先级 primary(fresh) → sina。

    primary_source：主源标签（simnow/vvtr），用于 field_meta 与 quote.source 展示。
    eastmoney 参数仅为兼容旧调用保留，恒不参与合并。
    """
    now = float(now_ts if now_ts is not None else time.time())
    symbol = str(
        (simnow or {}).get("symbol")
        or (sina or {}).get("symbol")
        or ""
    )
    if not symbol:
        return None
    simnow_fresh = _simnow_is_fresh(
        simnow,
        sina,
        now_ts=now,
        stale_after=stale_after,
    )
    merged: dict[str, Any] = {"symbol": symbol.strip().lower()}
    field_meta: dict[str, dict[str, str]] = {}
    for field in _MERGE_FIELDS:
        selected: Any = None
        selected_source = "missing"
        if simnow_fresh and simnow and _valid_field(field, simnow.get(field)):
            selected = simnow[field]
            selected_source = primary_source
        elif sina and _valid_field(field, sina.get(field)):
            selected = sina[field]
            selected_source = "sina"
        elif simnow_fresh and simnow and field in _LEVEL_FIELDS:
            selected = []
        if selected is not None:
            merged[field] = selected
        field_meta[field] = {
            "source": selected_source,
            "quality": "direct" if selected_source != "missing" else "missing",
        }

    normalized = normalize_quote_fields(merged)
    if normalized is None:
        return None
    last_source = field_meta["last_price"]["source"]
    normalized["source"] = last_source
    normalized["field_meta"] = field_meta
    normalized["simnow_stale"] = bool(simnow) and not simnow_fresh
    if last_source == primary_source and simnow is not None:
        normalized["recv_ts"] = float(simnow.get("recv_ts") or now)
    elif sina is not None and sina.get("recv_ts") is not None:
        normalized["recv_ts"] = float(sina["recv_ts"])
    else:
        normalized["recv_ts"] = now
    return normalized


def merge_live_quotes(
    simnow_quotes: list[dict[str, Any]],
    sina_quotes: list[dict[str, Any]],
    eastmoney_quotes: list[dict[str, Any]] | None = None,  # 已弃用，兼容保留
    *,
    now_ts: float | None = None,
    stale_after: float = 90.0,
    primary_source: str = "simnow",
) -> list[dict[str, Any]]:
    """批量合并；保留主源顺序，再追加仅新浪存在的合约。

    优先级：primary(fresh) → sina。eastmoney 参数仅为兼容旧调用保留。
    """
    sim_index = {str(q.get("symbol") or "").lower(): q for q in simnow_quotes}
    sina_index = {str(q.get("symbol") or "").lower(): q for q in sina_quotes}
    symbols = list(sim_index)
    symbols.extend(symbol for symbol in sina_index if symbol not in sim_index)
    out: list[dict[str, Any]] = []
    for symbol in symbols:
        quote = merge_live_quote(
            sim_index.get(symbol),
            sina_index.get(symbol),
            now_ts=now_ts,
            stale_after=stale_after,
            primary_source=primary_source,
        )
        if quote is not None:
            out.append(quote)
    return out

