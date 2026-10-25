"""统一行情 quote 契约 —— 校验/补全字段（纯函数）

下游 kline / orderbook / trades / WS 依赖的字段集。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def normalize_tick_time_iso(raw: Any) -> str | None:
    """交易所时间戳归一为 "YYYY-MM-DD HH:MM:SS"（秒级）

    支持形态：
    - 17 位数字（VVTR v2：YYYYMMDDHHmmssSSS，毫秒截断）
    - "YYYY-MM-DD HH:MM:SS[.fff]" / "YYYY-MM-DDTHH:MM:SS"（VVTR v1 / iso）
    纯时刻（HH:MM:SS / HHmmss，无日期——新浪/CTP 展示形态）与垃圾输入
    返回 None：无日期的时间跨夜盘不可靠，消费方应回退本地钟
    （tick_time 原样保留供 UI 展示）。
    """
    t = str(raw or "").strip()
    if not t:
        return None
    try:
        if len(t) == 17 and t.isdigit():
            return datetime.strptime(t[:14], "%Y%m%d%H%M%S").strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        if len(t) >= 19 and t[4] == "-" and t[10] in " T":
            return datetime.strptime(
                t[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return None


def is_valid_quote(quote: dict[str, Any]) -> bool:
    """最低可用：symbol + 正 last_price"""
    symbol = str(quote.get("symbol") or "").strip()
    try:
        last = float(quote.get("last_price") or 0)
    except (TypeError, ValueError):
        return False
    return bool(symbol) and last > 0


def normalize_quote_fields(quote: dict[str, Any]) -> dict[str, Any] | None:
    """补全缺省字段，保证类型稳定；无效则返回 None"""
    if not is_valid_quote(quote):
        return None
    symbol = str(quote["symbol"]).strip()
    try:
        last = float(quote["last_price"])
        pre_raw = quote.get("pre_close")
        # 涨跌幅基准:昨收 → 昨结算(期货口径,VVTR v2 帧带 pre_settle);
        # 两者都缺(VVTR v1 深度帧无基准字段)时 pre=None——绝不用 last
        # 充当基准把涨跌幅算成 0.00%(2026-08-24 全市场 0.00% 事故),
        # 由上游(ws_bridge 昨收补全)或下游处理 None。
        settle = quote.get("settlement_price")
        try:
            settle_f = float(settle) if settle not in (None, "") else 0.0
        except (TypeError, ValueError):
            settle_f = 0.0
        pre = float(pre_raw) if pre_raw not in (None, "", 0, 0.0) else (
            settle_f if settle_f > 0 else 0.0
        )
        open_p = float(quote.get("open_price") or last)
        high = float(quote.get("high_price") or last)
        low = float(quote.get("low_price") or last)
        bid = float(quote.get("bid_price") or last)
        ask = float(quote.get("ask_price") or last)
        # 源自带 change(新浪/CTP)优先;基准缺失时不用 0 造 change
        src_change = quote.get("change")
        src_pct = quote.get("change_pct")
        if src_change not in (None, "", 0, 0.0) or pre > 0:
            change = float(src_change or (last - pre))
            change_pct = float(
                src_pct or ((change / pre) * 100 if pre > 0 else 0.0)
            )
        else:
            change = 0.0
            change_pct = 0.0
            pre = 0.0  # 无基准:change/pct 输出 None,pre_close 回退 last
        bid_vol = int(quote.get("bid_vol") or 0)
        ask_vol = int(quote.get("ask_vol") or 0)
        volume = int(quote.get("volume") or 0)
        position = int(quote.get("position") or 0)
        avg_raw = quote.get("avg_price")
        avg_price = float(avg_raw) if avg_raw not in (None, "", 0, 0.0) else 0.0
    except (TypeError, ValueError):
        return None

    has_base = pre > 0
    out: dict[str, Any] = {
        "symbol": symbol,
        "name": str(quote.get("name") or symbol),
        "exchange": str(quote.get("exchange") or ""),
        "last_price": last,
        # 无基准(昨收/昨结算均缺):change/pct 为 None,前端按缺省展示,
        # 不显示误导性的 0.00%
        "change": round(change, 10) if has_base else None,
        "change_pct": round(change_pct, 4) if has_base else None,
        "open_price": open_p,
        "high_price": high,
        "low_price": low,
        # 无基准:pre_close 亦 None(与 8-23 前 vvtr 透传行为一致),
        # ws_bridge 昨收补全据此识别
        "pre_close": pre if has_base else None,
        "bid_price": bid,
        "ask_price": ask,
        "bid_vol": max(0, bid_vol),
        "ask_vol": max(0, ask_vol),
        "volume": max(0, volume),
        "position": max(0, position),
        "avg_price": avg_price,
        "tick_time": str(quote.get("tick_time") or ""),
        "tick_time_iso": normalize_tick_time_iso(quote.get("tick_time")),
        "trade_date": str(quote.get("trade_date") or ""),
    }
    optional_float_fields = (
        "turnover",
        "settlement_price",
        "upper_limit_price",
        "lower_limit_price",
    )
    for field in optional_float_fields:
        value = quote.get(field)
        if value in (None, ""):
            out[field] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            out[field] = None
            continue
        out[field] = number if -1e20 < number < 1e20 and number > 0 else None

    pre_open_interest = quote.get("pre_open_interest")
    if pre_open_interest in (None, ""):
        out["pre_open_interest"] = None
    else:
        try:
            out["pre_open_interest"] = max(0, int(float(pre_open_interest)))
        except (TypeError, ValueError):
            out["pre_open_interest"] = None
    try:
        out["update_millisec"] = max(
            0, int(float(quote.get("update_millisec") or 0))
        )
    except (TypeError, ValueError):
        out["update_millisec"] = 0

    for field in ("bid_levels", "ask_levels"):
        levels: list[dict[str, Any]] = []
        raw_levels = quote.get(field)
        if isinstance(raw_levels, list):
            for raw_level in raw_levels[:5]:
                if not isinstance(raw_level, dict):
                    continue
                try:
                    price = float(raw_level.get("price") or 0)
                    level_volume = max(0, int(float(raw_level.get("volume") or 0)))
                except (TypeError, ValueError):
                    continue
                if 0 < price < 1e20:
                    levels.append({"price": price, "volume": level_volume})
        out[field] = levels

    # 透传来源/接收时间/锚价：virtual 脉冲与 K 线依赖
    if quote.get("source") is not None:
        out["source"] = str(quote.get("source") or "")
    if quote.get("recv_ts") is not None:
        try:
            out["recv_ts"] = float(quote["recv_ts"])
        except (TypeError, ValueError):
            pass
    if quote.get("decimal_places") is not None:
        try:
            out["decimal_places"] = int(quote["decimal_places"])
        except (TypeError, ValueError):
            pass
    if quote.get("anchor_price") is not None:
        try:
            ap = float(quote["anchor_price"])
            if ap > 0:
                out["anchor_price"] = ap
        except (TypeError, ValueError):
            pass
    return out


def filter_valid_quotes(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量规范化，丢弃无效项"""
    out: list[dict[str, Any]] = []
    for q in quotes:
        n = normalize_quote_fields(q)
        if n is not None:
            out.append(n)
    return out
