"""R3 时段感知口径测试 —— 边界掩码/开盘进场收益/日线退化"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.evaluate import (
    next_ret,
    position_from_factor,
    session_aware_metrics,
    session_aware_pnl,
    session_open_mask,
)


def _minute_bars(times_and_px: list[tuple[str, float, float]]) -> list[dict]:
    """[(time, open, close)] → bars"""
    return [
        {"time": t, "open": o, "close": c, "high": max(o, c), "low": min(o, c)}
        for t, o, c in times_and_px
    ]


def test_session_mask_lunch_kept_overnight_caught():
    bars = _minute_bars([
        ("2026-08-13 10:00", 100, 101),   # 日内
        ("2026-08-13 10:15", 101, 101),   # 小节后 15 分钟（保留）
        ("2026-08-13 11:30", 101, 100),   # 午休前
        ("2026-08-13 13:30", 100, 100),   # 午休恰好 2h（保留持仓，非边界）
        ("2026-08-13 15:00", 100, 100),   # 日盘收
        ("2026-08-13 21:00", 102, 103),   # 夜盘开（6h 缺口 → 边界）
        ("2026-08-13 23:00", 103, 102),   # 夜盘收
        ("2026-08-14 09:00", 99, 100),    # 次日日盘开（10h → 边界）
    ])
    m = session_open_mask(bars)
    # 午休（11:30→13:30）不是边界；21:00 与 09:00 是
    assert not m[3]
    assert m[5] and m[7]


def test_session_pnl_open_entry_math():
    """边界 bar 收益按 open→close、换手从空仓重进；非边界 bar 收盘对收盘"""
    bars = _minute_bars([
        ("2026-08-13 09:00", 100, 100),
        ("2026-08-13 09:15", 100, 110),   # 15 分钟间隔：非边界
        ("2026-08-13 21:00", 120, 126),   # 11h45m：边界
    ])
    pos = np.array([1.0, 1.0, 1.0])
    cost = 0.001
    pnl = session_aware_pnl(pos, bars, cost)
    # bar0：close→close=(110-100)/100，初始建仓换手 |1-0|
    assert abs(pnl[0] - (0.10 - cost)) < 1e-12
    # bar1：close→close=(126-110)/110，仓位不变无换手
    assert abs(pnl[1] - (16.0 / 110.0)) < 1e-12
    # bar2（边界）：open→close=(126-120)/120，从空仓重进换手 |1|
    assert abs(pnl[2] - ((126 - 120) / 120 - cost)) < 1e-12


def test_daily_bars_degrade_to_close_caliber():
    """日线无间隔信息（同日多 bar 不存在）→ 掩码全 False，口径=收盘对收盘"""
    bars = _minute_bars([
        (f"2026-08-{d:02d}", 100.0 + d, 100.0 + d) for d in range(1, 11)
    ])
    m = session_open_mask(bars)
    assert not m.any()
    factor = np.linspace(-1, 1, 10)
    pos = position_from_factor(factor)
    cost = 0.0
    pnl = session_aware_pnl(pos, bars, cost)
    close = np.array([b["close"] for b in bars], dtype=float)
    r = next_ret(close)
    prev = np.roll(pos, 1)
    prev[0] = 0.0
    expect = pos * r - np.abs(pos - prev) * cost
    assert np.allclose(pnl, expect)


def test_session_aware_metrics_fields():
    bars = _minute_bars([
        (f"2026-08-13 {h:02d}:00", 100.0 + h, 100.5 + h) for h in range(9, 23)
    ])
    factor = np.sin(np.linspace(0, 6, len(bars)))
    m = session_aware_metrics(factor, bars, cost=0.0003, periods=4032)
    assert set(m) == {"session_sortino", "session_ann_ret"}
    assert np.isfinite(m["session_sortino"]) and np.isfinite(m["session_ann_ret"])
