"""P5 分钟深历史测试 —— 交易日归属/换月平滑/中段空洞截断"""

from __future__ import annotations

import numpy as np

from app.data.tickdata_kline import (
    _apply_backward_adjustment,
    _bar_trading_day,
    detect_rolls,
)
from app.services.factor_mining.loader import _drop_big_gaps


def test_bar_trading_day_night_attribution():
    # 日盘归当日
    assert _bar_trading_day("2026-04-15 14:45:00") == "2026-04-15"
    # 夜盘 21:00 起归次一自然日（跨周末/节假日时投影仍保持与交易日边界单调）
    assert _bar_trading_day("2026-04-15 21:00:00") == "2026-04-16"
    assert _bar_trading_day("2026-04-15 23:45:00") == "2026-04-16"
    # 午夜后（凌晨）保持当日（夜盘归属同一交易日的后半夜）
    assert _bar_trading_day("2026-04-16 01:15:00") == "2026-04-16"
    # 日线无时刻 → 原样
    assert _bar_trading_day("2026-04-15") == "2026-04-15"


def test_minute_roll_adjustment_no_night_artifact():
    """换月日夜盘属新侧：平滑后边界应为真实隔夜跳幅而非 1/f 残留

    原始序列在换月处的价差 ≈ 换月因子 f（新/旧合约价差）。raw 边界
    -9.4%，平滑后旧侧 ×f 重述为新合约口径，边界收敛到真实隔夜 +0.5%。
    """
    f = 0.901
    old_close = 3000.0  # 旧合约口径
    night_close = old_close * f * 1.005  # 新合约实际价 ≈ 旧×f×隔夜+0.5%
    bars = [
        {"time": "2026-04-15 14:45:00", "close": old_close, "open": old_close},
        {"time": "2026-04-15 21:00:00", "close": night_close, "open": night_close},
        {"time": "2026-04-16 09:00:00", "close": night_close * 1.001, "open": night_close},
    ]
    raw_jump = night_close / old_close - 1
    assert raw_jump < -0.09  # 未平滑时边界是 ~-9.4% 的换月跳空
    out = _apply_backward_adjustment(bars, [("2026-04-15", f)])
    # 14:45 旧侧 ×f；21:00 夜盘新侧不乘
    assert abs(out[0]["close"] - old_close * f) < 1e-6
    assert abs(out[1]["close"] - night_close) < 1e-6
    # 边界跳幅收敛到真实隔夜变化（+0.5%），无 1/f≈+11% 残留
    jump = out[1]["close"] / out[0]["close"] - 1
    assert abs(jump - 0.005) < 1e-9


def test_detect_rolls_basic():
    from datetime import date, datetime

    def _d(s: str):
        return datetime.combine(date.fromisoformat(s), datetime.min.time())

    rows = [
        {"ts": _d("2026-04-14"), "instrument": "ma2605", "volume": 100, "close": 3100.0},
        {"ts": _d("2026-04-14"), "instrument": "ma2609", "volume": 10, "close": 2800.0},
        {"ts": _d("2026-04-15"), "instrument": "ma2605", "volume": 10, "close": 3110.0},
        {"ts": _d("2026-04-15"), "instrument": "ma2609", "volume": 200, "close": 2801.0},
    ]
    rolls = detect_rolls(rows)
    # f = d0(04-14) 当天新主力收盘 / 旧主力收盘 = 2800/3100
    assert rolls == [("2026-04-14", 2800.0 / 3100.0)]


def _b(t: str) -> dict:
    return {"time": t, "close": 100.0}


def test_drop_big_gaps_keeps_recent_tail():
    bars = (
        [_b(f"2025-01-{d:02d} 09:00:00") for d in range(1, 11)]  # 10 根（旧段）
        + [_b(f"2025-06-{d:02d} 09:00:00") for d in range(1, 21)]  # 20 根（最新段）
    )
    out = _drop_big_gaps(bars, max_gap_days=15)
    # 截断到最后一个空洞：保最新段（即使它更短，也要贴近当前市况）
    assert out[0]["time"].startswith("2025-06")
    assert len(out) == 20


def test_drop_big_gaps_keeps_recency_over_length():
    bars = (
        [_b(f"2024-01-{d:02d} 09:00:00") for d in range(1, 25)]  # 24 根旧段
        + [_b(f"2025-01-{d:02d} 09:00:00") for d in range(1, 6)]  # 5 根最新段
    )
    out = _drop_big_gaps(bars, max_gap_days=15)
    assert out[0]["time"].startswith("2025-01")
    assert len(out) == 5


def test_drop_big_gaps_no_gap_unchanged():
    bars = [_b(f"2025-01-{d:02d} 09:00:00") for d in range(1, 11)]
    assert _drop_big_gaps(bars) is bars or _drop_big_gaps(bars) == bars


def test_drop_big_gaps_prefers_recent_on_tie():
    bars = (
        [_b(f"2024-01-{d:02d} 09:00:00") for d in range(1, 6)]
        + [_b(f"2025-01-{d:02d} 09:00:00") for d in range(1, 6)]
    )
    out = _drop_big_gaps(bars, max_gap_days=15)
    assert out[0]["time"].startswith("2025-01")
