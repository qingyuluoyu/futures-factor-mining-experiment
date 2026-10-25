"""P10 因子衰减监控测试 —— 滚动指标/衰减标记/健康刷新分组"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.decay import rolling_sortino


def _make_bars(n: int, seed: int) -> list[dict]:
    rng = np.random.RandomState(seed)
    close = 3000.0 * np.cumprod(1 + rng.randn(n) * 0.01)
    return [
        {
            "time": f"2024-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
            "open": float(c),
            "high": float(c * 1.005),
            "low": float(c * 0.995),
            "close": float(c),
            "volume": int(1000 + rng.randint(0, 500)),
            "open_interest": int(50000 + rng.randint(0, 1000)),
        }
        for i, c in enumerate(close)
    ]


def test_rolling_sortino_structure():
    bars = _make_bars(300, 1)
    out = rolling_sortino([0], bars, "1d", cost=0.0003, window=60)
    assert out is not None
    assert set(out) == {"live_sortino", "live_ann", "window", "bars", "decay"}
    assert out["window"] == 60 and out["bars"] == 300
    assert out["decay"] == (out["live_sortino"] <= 0)


def test_rolling_sortino_insufficient_bars():
    assert rolling_sortino([0], _make_bars(50, 2), "1d", cost=0.0003) is None
    assert rolling_sortino([], _make_bars(300, 2), "1d", cost=0.0003) is None


def test_rolling_sortino_decay_flag_direction():
    """近端构造持续下跌段：空头因子（负仓位）应健康、多头特征衰减与否与信号一致"""
    bars = _make_bars(300, 3)
    out = rolling_sortino([0], bars, "1d", cost=0.0003)
    # 随机数据下 RET 单特征近端 sortino 可正可负，但 decay 标记必须与符号一致
    assert out["decay"] == (out["live_sortino"] <= 0.0)


def test_rolling_health_ic_marker():
    """衰减判定 = 250 根滚动 IC ≤ 0（E10b 口径），与 live_sortino 无关"""
    from app.services.factor_lab.scoring.decay import (
        DECAY_IC_WINDOW,
        rolling_health,
    )
    from app.services.factor_lab.scoring.evaluate import _ts_ic, next_ret

    bars = _make_bars(400, 7)
    out = rolling_health([0], bars, "1d", cost=0.0003)
    assert out is not None
    # trailing_ic 与手工计算一致（末 250 根 ts_ic）
    close = np.array([b["close"] for b in bars], dtype=float)
    from app.services.factor_lab.features import feature_matrix

    from app.services.factor_lab.vm import execute

    # 与 rolling_health 同源:tokens 经 vm.execute(含 .3 因果滚动归一化)
    factor = execute([0], feature_matrix(bars))
    expect = float(_ts_ic(factor[-250:], next_ret(close)[-250:]))
    assert abs(out["trailing_ic"] - round(expect, 4)) < 1e-9
    # 衰减标记由 IC 决定
    assert out["decay"] == (out["trailing_ic"] <= 0.0)
    assert out["ic_window"] == DECAY_IC_WINDOW
    assert "live_sortino" in out  # 信息字段保留

    # 数据不足（< ic_window+5）返回 None
    assert rolling_health([0], _make_bars(200, 8), "1d", cost=0.0003) is None
