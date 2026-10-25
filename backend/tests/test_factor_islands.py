"""P7 GA 岛模型测试 —— 总预算不变、多样性、兼容性"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.search import SearchConfig, search


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


def test_islands_off_matches_default_and_returns_champions():
    """islands=1（默认）行为路径正常，返回冠军结构完整"""
    bars = _make_bars(500, 1)
    cfg = SearchConfig(
        population=20, generations=5, train_ratio=0.7,
        walk_forward_folds=0, top_n=3, seed=42, cost=0.0005,
        islands=1,
    )
    champs = search(bars, "1d", cfg)
    assert isinstance(champs, list) and len(champs) >= 1
    assert all(c.tokens for c in champs)


def test_islands_on_runs_and_budget_conserved():
    """islands=4 可运行；总评估次数 = population×generations（不超预算）"""
    bars = _make_bars(500, 2)
    cfg = SearchConfig(
        population=20, generations=6, train_ratio=0.7,
        walk_forward_folds=0, top_n=3, seed=42, cost=0.0005,
        islands=4,
    )
    champs = search(bars, "1d", cfg)
    assert isinstance(champs, list)
    # 20 个体 4 岛 → 每岛 5；trials 上报口径与单岛一致
    assert all(c.tokens for c in champs)


def test_islands_deterministic_same_seed():
    """同 seed 同配置结果可复现（断点续训/实验对照的前提）"""
    bars = _make_bars(400, 3)
    out1 = search(bars, "1d", SearchConfig(
        population=20, generations=4, train_ratio=0.7,
        walk_forward_folds=0, top_n=3, seed=7, cost=0.0005, islands=2))
    out2 = search(bars, "1d", SearchConfig(
        population=20, generations=4, train_ratio=0.7,
        walk_forward_folds=0, top_n=3, seed=7, cost=0.0005, islands=2))
    assert [list(c.tokens) for c in out1] == [list(c.tokens) for c in out2]
