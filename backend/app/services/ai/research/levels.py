"""交易点位计算 —— 入场/支撑/压力/止损/止盈（纯函数）"""

from __future__ import annotations

from typing import TypedDict


class TradeLevels(TypedDict):
    """可操作点位包"""

    entry: float | None
    support: float | None
    resistance: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None


def _r(value: float | None) -> float | None:
    """价格四舍五入。"""
    if value is None:
        return None
    return round(float(value), 4)


def _rr(entry: float | None, stop: float | None, target: float | None) -> float | None:
    """盈亏比 = 盈利空间 / 风险空间。"""
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 1e-12:
        return None
    return round(reward / risk, 2)


def compute_long_levels(
    last_price: float | None,
    ma10: float | None,
    ma20: float | None,
    high20: float | None,
    low20: float | None,
) -> TradeLevels:
    """做多点位：回踩入场、下方支撑/止损、上方压力/止盈。"""
    # 入场：优先 MA10 回踩，否则现价
    entry = ma10 if ma10 is not None else last_price
    if entry is not None and last_price is not None and entry > last_price:
        # 已远离均线时用现价追多不理想，仍给出现价作参考入场
        entry = last_price

    # 支撑：近低与 MA20 较高者（更近）
    support_candidates = [v for v in (low20, ma20) if v is not None]
    support = max(support_candidates) if support_candidates else None
    if support is None and entry is not None:
        support = entry * 0.985

    # 压力：近 20 高
    resistance = high20
    if resistance is None and last_price is not None:
        resistance = last_price * 1.02

    # 止损：支撑下方一点，或入场的 1.5%
    stop_loss: float | None = None
    if support is not None:
        stop_loss = support * 0.995
    elif entry is not None:
        stop_loss = entry * 0.985

    # 止盈：压力位；若过近则用 1.5R
    take_profit = resistance
    if entry is not None and stop_loss is not None:
        risk = abs(entry - stop_loss)
        min_tp = entry + risk * 1.5
        if take_profit is None or take_profit <= entry:
            take_profit = min_tp
        elif take_profit < min_tp:
            # 压力太近时至少给 1.5R
            take_profit = min_tp

    return {
        "entry": _r(entry),
        "support": _r(support),
        "resistance": _r(resistance),
        "stop_loss": _r(stop_loss),
        "take_profit": _r(take_profit),
        "risk_reward": _rr(entry, stop_loss, take_profit),
    }


def compute_short_levels(
    last_price: float | None,
    ma10: float | None,
    ma20: float | None,
    high20: float | None,
    low20: float | None,
) -> TradeLevels:
    """做空点位：反弹入场、上方压力/止损、下方支撑/止盈。"""
    entry = ma10 if ma10 is not None else last_price
    if entry is not None and last_price is not None and entry < last_price:
        entry = last_price

    resistance_candidates = [v for v in (high20, ma20) if v is not None]
    resistance = min(resistance_candidates) if resistance_candidates else None
    if resistance is None and entry is not None:
        resistance = entry * 1.015

    support = low20
    if support is None and last_price is not None:
        support = last_price * 0.98

    stop_loss: float | None = None
    if resistance is not None:
        stop_loss = resistance * 1.005
    elif entry is not None:
        stop_loss = entry * 1.015

    take_profit = support
    if entry is not None and stop_loss is not None:
        risk = abs(stop_loss - entry)
        min_tp = entry - risk * 1.5
        if take_profit is None or take_profit >= entry:
            take_profit = min_tp
        elif take_profit > min_tp:
            take_profit = min_tp

    return {
        "entry": _r(entry),
        "support": _r(support),
        "resistance": _r(resistance),
        "stop_loss": _r(stop_loss),
        "take_profit": _r(take_profit),
        "risk_reward": _rr(entry, stop_loss, take_profit),
    }


def empty_levels(last_price: float | None) -> TradeLevels:
    """无明确趋势时的空点位。"""
    return {
        "entry": _r(last_price),
        "support": None,
        "resistance": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward": None,
    }
