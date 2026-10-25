"""P5 统计严谨性测试 —— PBO 代理、保守 OOS、champion 数量收缩"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab import execute, feature_matrix
from app.services.factor_lab.scoring.evaluate import evaluate_factor
from app.services.factor_lab.search import SearchConfig, _dedup_top, search


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


def _champ_input(bars, cost=0.0005):
    mat = feature_matrix(bars)
    close = np.array([b["close"] for b in bars], dtype=float)
    out = []
    for feat in (0, 1, 2, 10, 12):
        m = evaluate_factor(mat[feat], close, cost=cost, periods=243)
        out.append((float(m["composite"]), [feat], m))
    return out


def test_champion_count_shrinks_with_trials():
    """trials 少时 champion 数被 √ 收缩（多重检验修正）"""
    bars = _make_bars(600, seed=1)
    train, test = bars[:420], bars[420:]
    champs_in = _champ_input(train)
    out_few = _dedup_top(
        champs_in, top_n=10, test_bars=test, timeframe="1d", cost=0.0005,
        use_test=True, walk_forward_folds=0, train_bars=train, trials=24,
    )
    # √24/2 ≈ 2.4 → 收缩到 2
    assert len(out_few) <= 2
    out_many = _dedup_top(
        champs_in, top_n=10, test_bars=test, timeframe="1d", cost=0.0005,
        use_test=True, walk_forward_folds=0, train_bars=train, trials=1200,
    )
    # √1200/2 ≈ 17 → 不超过 top_n=10
    assert len(out_many) <= 10


def test_pbo_proxy_and_conservive_oos_attached():
    """通过严格筛的 champion 携带 pbo_proxy / oos_conservative / trials"""
    bars = _make_bars(800, seed=2)
    train, test = bars[:560], bars[560:]
    out = _dedup_top(
        _champ_input(train), top_n=10, test_bars=test, timeframe="1d",
        cost=0.0005, use_test=True, walk_forward_folds=0,
        train_bars=train, trials=450,
    )
    assert out, "应有冠军产出"
    for c in out:
        m = c.metrics
        if m.get("overfit_warning"):
            continue  # 兜底的不带 pbo
        assert m.get("trials") == 450
        assert "pbo_proxy" in m and 0.0 <= m["pbo_proxy"] <= 1.0
        # 测试段 240 根 ≥ 4×30，保守 OOS 应存在且 ≤ 测试段 sortino
        assert "oos_conservative" in m


def test_no_trials_backward_compatible():
    """trials=0（旧调用方式）不收缩、不标注"""
    bars = _make_bars(400, seed=3)
    out = _dedup_top(
        _champ_input(bars), top_n=5, timeframe="1d", cost=0.0005,
        use_test=False, walk_forward_folds=0,
    )
    assert len(out) == 5
    assert all("pbo_proxy" not in c.metrics for c in out)


def test_search_end_to_end_carries_stats():
    """search() 全链路：响应冠军带统计严谨性字段"""
    bars = _make_bars(700, seed=4)
    champs = search(
        bars,
        "1d",
        SearchConfig(
            population=15, generations=4, top_n=5,
            train_ratio=0.7, walk_forward_folds=3,
        ),
    )
    trials_expected = 15 * 4
    for c in champs:
        assert c.metrics.get("trials") == trials_expected
