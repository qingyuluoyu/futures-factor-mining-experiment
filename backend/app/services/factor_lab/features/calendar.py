"""日历特征 —— 交易日内时钟进度 / 夜盘标记 / 周内日 / 月内位置

自单文件 features.py 拆出（2026-08-31，行为零变化）。
时间类特征本身已在 [-1,1] 且带日内周期语义，不做 zscore。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

# 交易日从 21:00（夜盘开盘）起算，跨到次日 15:00 收盘，共 18 小时
_DAY_START_MIN = 21 * 60
_DAY_SPAN_MIN = 18 * 60
# 夜盘时段：21:00 之后，或次日 03:00 之前（贵金属等跨夜品种）
_NIGHT_END_MIN = 3 * 60


def _clock_minutes(bar_time: str) -> int | None:
    """从 bar 时间串取当日时钟分钟数；日线（无时刻部分）返回 None"""
    text = bar_time.strip()
    if len(text) < 16:
        return None
    try:
        return int(text[11:13]) * 60 + int(text[14:16])
    except ValueError:
        return None


def _time_of_day(bars: list[dict[str, Any]]) -> np.ndarray:
    """交易日内时钟进度，映射到 [-1, 1]

    21:00 夜盘开盘为 -1，次日 15:00 收盘为 +1。只用 bar 自身的时钟，
    不依赖「当日共几根」，因此没有未来信息。日线恒为 0。
    """
    out = np.zeros(len(bars), dtype=float)
    for i, bar in enumerate(bars):
        minutes = _clock_minutes(str(bar.get("time") or ""))
        if minutes is None:
            continue
        elapsed = (minutes - _DAY_START_MIN) % (24 * 60)
        out[i] = min(elapsed / _DAY_SPAN_MIN, 1.0) * 2.0 - 1.0
    return out


def _is_night(bars: list[dict[str, Any]]) -> np.ndarray:
    """夜盘标记：21:00 之后或次日 03:00 之前为 1，日线恒 0"""
    out = np.zeros(len(bars), dtype=float)
    for i, bar in enumerate(bars):
        minutes = _clock_minutes(str(bar.get("time") or ""))
        if minutes is None:
            continue
        if minutes >= _DAY_START_MIN or minutes < _NIGHT_END_MIN:
            out[i] = 1.0
    return out


def _dow_dom(bars: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """周内日（周一=-1..周五=+1）与月内位置（[−1,1]），日线/分钟通用

    从 bar 日期取 weekday——注意期货交易日（夜盘归属次日）与自然日不同，
    此处用自然日 weekday（简单且因果）；无日期返回 0。
    """
    n = len(bars)
    dow = np.zeros(n, dtype=float)
    dom = np.zeros(n, dtype=float)
    for i, bar in enumerate(bars):
        t = str(bar.get("time") or "")[:10]
        if len(t) < 10:
            continue
        try:
            d = date.fromisoformat(t)
        except ValueError:
            continue
        dow[i] = (d.weekday() - 2) / 2.0  # Mon=-1 .. Fri=+1
        dom[i] = (d.day - 15.5) / 15.5
    return dow, dom
