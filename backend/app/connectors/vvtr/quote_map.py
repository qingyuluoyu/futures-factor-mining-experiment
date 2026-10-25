"""VVTR 行情消息 → 统一 quote 契约（纯函数）

覆盖两种输入：
1. WS v2 type=14 国内期货深度推送（扁平字段 + 5 档 q）
2. HTTP /snapshot 快照（L1，期货 1 档 q）

输出交给 data.quote_schema.normalize_quote_fields 规范化后写入
market:live:source:vvtr:quote:{symbol}。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.connectors.vvtr.config import VVTR_SOURCE, is_master_symbol, to_vvtr_symbol

_BJ_TZ = timezone(timedelta(hours=8))

# 夜盘归属下一交易日的时段边界（粗粒度；节假日无法离线判定，按工作日推算）
_NIGHT_START_HOUR = 20
_NIGHT_END_HOUR = 3  # 凌晨 00:00-03:00 视为夜盘尾段


def _f(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == 0 or not (-1e20 < number < 1e20):
        return None
    return number


def _i(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _next_weekday(day: datetime) -> datetime:
    """下一个工作日（跳过周末；节假日由下游 freshness 逻辑兜底）"""
    cur = day + timedelta(days=1)
    while cur.weekday() >= 5:  # 5=周六 6=周日
        cur += timedelta(days=1)
    return cur


def compute_trade_date(tick_time: str) -> str:
    """由推送时间推算交易日（CTP 语义：夜盘归下一工作日）

    VVTR 的 t 为自然时间；日盘直接取日期，夜盘（20:00 后 / 03:00 前）
    归属下一个工作日，与 sina trade_date 对齐避免 freshness 误判。
    """
    raw = str(tick_time or "").strip()
    # WS v2 type=14：YYYYMMDDHHmmssSSS（17 位）
    if len(raw) == 17 and raw.isdigit():
        try:
            local = datetime(
                int(raw[0:4]), int(raw[4:6]), int(raw[6:8]),
                int(raw[8:10]), int(raw[10:12]),
                tzinfo=_BJ_TZ,
            )
        except ValueError:
            return ""
    else:
        try:
            local = datetime.fromisoformat(raw).replace(tzinfo=_BJ_TZ)
        except ValueError:
            return ""
    if local.hour >= _NIGHT_START_HOUR or local.hour < _NIGHT_END_HOUR:
        local = _next_weekday(local)
    return local.strftime("%Y-%m-%d")


def _levels(quotes: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """q 数组 → (bids, asks) 五档（缺失档位剔除；价格 0 视为无档）"""
    bids: list[dict[str, Any]] = []
    asks: list[dict[str, Any]] = []
    for level in (quotes or [])[:5]:
        if not isinstance(level, dict):
            continue
        bp = _f(level.get("bP"))
        ap = _f(level.get("aP"))
        bv = _i(level.get("bV"))
        av = _i(level.get("aV"))
        if bp is not None:
            bids.append({"price": bp, "volume": max(0, bv or 0)})
        if ap is not None:
            asks.append({"price": ap, "volume": max(0, av or 0)})
    return bids, asks


def _decorate(quote: dict[str, Any]) -> dict[str, Any]:
    """补 name/exchange/decimal_places（内部合约表反查）"""
    try:
        from app.data.contracts import ALL_CONTRACTS, get_decimal_places
        from app.data.contracts import get_code_for_symbol

        symbol = str(quote.get("symbol") or "")
        code = get_code_for_symbol(symbol)
        quote["decimal_places"] = get_decimal_places(code) if code else None
        for contract in ALL_CONTRACTS:
            if contract.get("symbol") == symbol:
                quote.setdefault("name", contract.get("name") or "")
                if not quote.get("exchange"):
                    quote["exchange"] = contract.get("exchange") or ""
                break
    except Exception:
        pass
    return quote


def parse_vvtr_frames(text: str) -> list[dict[str, Any]]:
    """WSS 原始帧 → 行情 dict 列表（v1 双重 JSON 编码兼容）

    v1 推送形如 ["{\\"t\\":…}","{\\"t\\":…}"]——外层数组元素是再包了
    一层 JSON 的字符串，必须逐元素二次解析；v2 与 welcome 控制帧为
    普通对象/对象数组。整体非 JSON 返回 []；坏元素跳过。
    """
    try:
        messages = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(messages, list):
        messages = [messages]
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(m, dict):
            out.append(m)
    return out


def vvtr_ws_message_to_quote(msg: dict[str, Any]) -> dict[str, Any] | None:
    """WS v2 type=14 推送 → 统一 quote（无效/主力合成合约返回 None）"""
    if not isinstance(msg, dict):
        return None
    symbol = str(msg.get("s") or "").strip().lower()
    if not symbol or is_master_symbol(symbol):
        return None
    last = _f(msg.get("p"))
    if last is None:
        return None

    bids, asks = _levels(msg.get("q") if isinstance(msg.get("q"), list) else [])
    tick_time_raw = str(msg.get("t") or "")
    # 涨跌幅基准（期货口径）= 昨结算价：统一契约的 pre_close 必须装昨结
    # （与新浪/CTP 源一致，normalize_quote_fields 以 pre_close 为基准）。
    # 帧 pre_settle=昨结、pre_close=昨收：优先 昨结，缺失才回退昨收——
    # 否则 merge 链重算 change_pct 会按昨收算出股票口径的错误百分比。
    pre_settle = _f(msg.get("pre_settle"))
    frame_pre_close = _f(msg.get("pre_close"))
    quote: dict[str, Any] = {
        "symbol": symbol,
        "last_price": last,
        "open_price": _f(msg.get("open")),
        "high_price": _f(msg.get("high")),
        "low_price": _f(msg.get("low")),
        "pre_close": pre_settle if pre_settle else frame_pre_close,
        "settlement_price": pre_settle,  # 昨结算
        # 源端 price_chg/radio 的基准口径不可考：丢弃，交 normalize_quote_fields
        # 用上方修正后的 pre_close(昨结)统一重算，避免两套口径混显
        "change": None,
        "change_pct": None,
        "volume": _i(msg.get("total_vol")),
        "position": _i(msg.get("open_int")),
        "turnover": _f(msg.get("turnover")),
        "avg_price": _f(msg.get("avg_price")),
        "upper_limit_price": _f(msg.get("upper_limit")),
        "lower_limit_price": _f(msg.get("low_limit")),
        "bid_levels": bids,
        "ask_levels": asks,
        "source": VVTR_SOURCE,
        "tick_time": tick_time_raw,
        "trade_date": compute_trade_date(tick_time_raw),
    }
    if bids:
        quote["bid_price"] = bids[0]["price"]
        quote["bid_vol"] = bids[0]["volume"]
    if asks:
        quote["ask_price"] = asks[0]["price"]
        quote["ask_vol"] = asks[0]["volume"]
    return _decorate(quote)


def vvtr_snapshot_to_quote(snap: dict[str, Any]) -> dict[str, Any] | None:
    """HTTP /snapshot 条目 → 统一 quote（L1：期货 1 档）"""
    if not isinstance(snap, dict):
        return None
    symbol = str(snap.get("s") or "").strip().lower()
    if not symbol or is_master_symbol(symbol):
        return None
    last = _f(snap.get("p"))
    if last is None:
        return None

    bids, asks = _levels(snap.get("q") if isinstance(snap.get("q"), list) else [])
    quote: dict[str, Any] = {
        "symbol": symbol,
        "last_price": last,
        "open_price": _f(snap.get("o")),
        "high_price": _f(snap.get("h")),
        "low_price": _f(snap.get("l")),
        "volume": _i(snap.get("cV")),
        "position": _i(snap.get("cP")),
        "bid_levels": bids,
        "ask_levels": asks,
        "source": VVTR_SOURCE,
        "recv_ts": None,
    }
    if bids:
        quote["bid_price"] = bids[0]["price"]
        quote["bid_vol"] = bids[0]["volume"]
    if asks:
        quote["ask_price"] = asks[0]["price"]
        quote["ask_vol"] = asks[0]["volume"]
    return _decorate(quote)


def vvtr_v1_message_to_quote(msg: dict[str, Any]) -> dict[str, Any] | None:
    """WS v1 L1 tick → 统一 quote（期货 1 档，约 1s 批量推送）

    v1 字段与 /snapshot 基本同构（s/o/h/l/p/cV/cA/cP/tt/lV/lA），
    盘口数组字段名为 quotes（快照为 q）；t 为 "YYYY-MM-DD HH:MM:SS"
    北京时间字符串，缺失时按本地接收时间推算交易日（夜盘归下一工作日）。
    """
    if not isinstance(msg, dict):
        return None
    symbol = str(msg.get("s") or "").strip().lower()
    if not symbol or is_master_symbol(symbol):
        return None
    last = _f(msg.get("p"))
    if last is None:
        return None

    raw_levels = msg.get("quotes")
    if not isinstance(raw_levels, list):
        raw_levels = msg.get("q") if isinstance(msg.get("q"), list) else []
    bids, asks = _levels(raw_levels)
    tick_time_raw = str(msg.get("t") or "")
    quote: dict[str, Any] = {
        "symbol": symbol,
        "last_price": last,
        "open_price": _f(msg.get("o")),
        "high_price": _f(msg.get("h")),
        "low_price": _f(msg.get("l")),
        "volume": _i(msg.get("cV")),
        "position": _i(msg.get("cP")),
        "turnover": _f(msg.get("cA")),
        "bid_levels": bids,
        "ask_levels": asks,
        "source": VVTR_SOURCE,
        "tick_time": tick_time_raw,
        "trade_date": compute_trade_date(
            tick_time_raw
            if tick_time_raw
            else datetime.now(_BJ_TZ).strftime("%Y%m%d%H%M%S000")
        ),
    }
    if bids:
        quote["bid_price"] = bids[0]["price"]
        quote["bid_vol"] = bids[0]["volume"]
    if asks:
        quote["ask_price"] = asks[0]["price"]
        quote["ask_vol"] = asks[0]["volume"]
    return _decorate(quote)


__all__ = [
    "compute_trade_date",
    "parse_vvtr_frames",
    "to_vvtr_symbol",
    "vvtr_snapshot_to_quote",
    "vvtr_v1_message_to_quote",
    "vvtr_ws_message_to_quote",
]
