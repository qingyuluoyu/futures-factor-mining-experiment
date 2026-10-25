"""P2 组合因子策略测试 —— 多公式加权 tanh 仓位 + 向后兼容"""

from __future__ import annotations

import numpy as np

from app.services.ai_trading.strategies.factor import (
    _latest_position,
    compute_factor_signal,
    factor_snapshot,
    normalize_factor_params,
)


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


def test_normalize_single_backward_compat():
    """单公式（扁平）输入：输出保持扁平，无 factor_weights 键"""
    p = normalize_factor_params({"factor_tokens": [8, 20]})
    assert p["factor_tokens"] == [8, 20]
    assert "factor_weights" not in p
    assert p["factor_text"]


def test_normalize_combo_and_weights():
    p = normalize_factor_params(
        {"factor_tokens": [[8], [10], [12]], "factor_weights": [1, 3, 0]}
    )
    # 零权重非法 → 整组回退等权
    assert "factor_weights" in p
    assert p["factor_weights"] == [round(1 / 3, 6)] * 3
    assert len(p["factor_tokens"]) == 3
    assert "⊕" in p["factor_text"]

    p2 = normalize_factor_params(
        {"factor_tokens": [[8], [10]], "factor_weights": [3, 1]}
    )
    assert p2["factor_weights"] == [0.75, 0.25]


def test_normalize_combo_caps_groups():
    """超过 5 个成员截断，防误传超长结构"""
    groups = [[i] for i in range(8)]
    p = normalize_factor_params({"factor_tokens": groups})
    assert len(p["factor_tokens"]) == 5


def test_latest_position_combo_weighted():
    bars = _make_bars(120, seed=5)
    pos_a = _latest_position(bars, [8])  # DEV
    pos_b = _latest_position(bars, [10])  # RSI14
    assert pos_a is not None and pos_b is not None
    # 等权组合 = 两成员均值
    combo = _latest_position(bars, [[8], [10]])
    assert combo is not None
    assert abs(combo - (pos_a + pos_b) / 2) < 1e-9
    # 3:1 加权
    combo_w = _latest_position(bars, [[8], [10]], weights=[3.0, 1.0])
    assert abs(combo_w - (3 * pos_a + pos_b) / 4) < 1e-9


def test_latest_position_invalid_member_skipped():
    bars = _make_bars(120, seed=6)
    # 第二成员 token 无效（特征越界 → execute None）：跳过并权重重归一
    pos = _latest_position(bars, [[8], [999]])
    assert pos is not None
    pos_single = _latest_position(bars, [8])
    assert abs(pos - pos_single) < 1e-9


def test_compute_signal_combo_flow():
    bars = _make_bars(120, seed=7)
    p = normalize_factor_params({"factor_tokens": [[8], [10]]})
    sig = compute_factor_signal(bars, p, "both", None)
    assert sig["action"] in ("open_long", "open_short", "hold", "close")
    assert sig["parse_ok"]
    assert "factor_position" in sig.get("factor_snapshot", {})
    # side_mode 限制仍生效
    pos = _latest_position(bars, [[8], [10]])
    if pos is not None and pos > 0.3:
        sig_lo = compute_factor_signal(bars, p, "long_only", None)
        assert sig_lo["action"] == "open_long"


def test_factor_snapshot_combo():
    bars = _make_bars(120, seed=8)
    snap = factor_snapshot([[8], [10]], bars)
    assert snap is not None
    assert "⊕" in snap["text"]
    assert -1.0 <= snap["position"] <= 1.0
    # 扁平兼容
    snap1 = factor_snapshot([8], bars)
    assert snap1 is not None and "⊕" not in snap1["text"]
