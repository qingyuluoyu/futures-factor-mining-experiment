"""P8 市场状态分解测试 —— 掩码互斥覆盖/趋势方向/分解指标"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.regime import (
    regime_decompose,
    regime_masks,
)


def _trending_bars(n: int, drift: float, seed: int = 1) -> list[dict]:
    rng = np.random.RandomState(seed)
    close = 3000.0 * np.cumprod(1 + drift + rng.randn(n) * 0.005)
    return [
        {
            "time": f"2024-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
            "open": float(c),
            "high": float(c * 1.005),
            "low": float(c * 0.995),
            "close": float(c),
            "volume": 1000,
            "open_interest": 50000,
        }
        for i, c in enumerate(close)
    ]


def test_masks_exclusive_and_exhaustive():
    rng = np.random.RandomState(2)
    close = 3000.0 * np.cumprod(1 + rng.randn(500) * 0.01)
    m = regime_masks(close)
    up, dn, chop = m["trend_up"], m["trend_down"], m["chop"]
    # 互斥且覆盖
    assert not np.any(up & dn)
    assert np.all(up | dn | chop)
    assert up.sum() + dn.sum() + chop.sum() == len(close)


def test_masks_detect_trend_direction():
    up = regime_masks(np.asarray([b["close"] for b in _trending_bars(300, +0.004, 3)]))
    dn = regime_masks(np.asarray([b["close"] for b in _trending_bars(300, -0.004, 3)]))
    # 强上行大部分时间判为趋势上；强下行判为趋势下
    assert up["trend_up"].sum() > up["trend_down"].sum()
    assert dn["trend_down"].sum() > dn["trend_up"].sum()
    # 纯随机游走以震荡为主
    rng = np.random.RandomState(4)
    rw = regime_masks(3000.0 * np.cumprod(1 + rng.randn(400) * 0.005))
    assert rw["chop"].sum() > 0.5 * 400


def test_regime_decompose_structure():
    bars = _trending_bars(400, 0.0, seed=5)
    out = regime_decompose([0], bars, "1d", 0.0003)  # RET 特征
    assert out is not None
    assert set(out) == {"trend_up", "trend_down", "chop"}
    total = sum(seg["bars"] for seg in out.values())
    assert total == len(bars) - 1  # 首根无 pnl 被排除
    for seg in out.values():
        if seg["sortino"] is not None:
            assert seg["ann_ret"] is not None
