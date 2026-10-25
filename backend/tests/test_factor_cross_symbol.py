"""P6 跨品种验证测试 —— 板块映射/判定门/指标记录"""

from __future__ import annotations

import numpy as np

from app.data.product_sectors import get_cross_peers, get_sector
from app.services.factor_lab.cross_symbol import cross_validate_tokens
from app.services.factor_lab.search import _dedup_top


def _make_bars(n: int, seed: int, drift: float = 0.0) -> list[dict]:
    rng = np.random.RandomState(seed)
    close = 3000.0 * np.cumprod(1 + drift + rng.randn(n) * 0.01)
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


def test_sector_mapping():
    assert get_sector("rb") == "ferrous"
    assert get_sector("RB") == "ferrous"
    assert get_sector("xyz") is None
    peers = get_cross_peers("rb")
    assert "rb" not in peers
    assert "hc" in peers and "i" in peers
    assert len(peers) == 4


def test_cross_validate_tokens_majority_rule():
    """≥(K+1)//2 个伙伴为正才通过；无伙伴时放行"""
    peers = [("hc", _make_bars(400, 1)), ("i", _make_bars(400, 2)),
             ("j", _make_bars(400, 3)), ("jm", _make_bars(400, 4))]
    ok, scores = cross_validate_tokens([0], peers, "1d", 0.0003)
    assert set(scores) == {"hc", "i", "j", "jm"}
    positives = sum(1 for v in scores.values() if v > 0)
    # K=4 → need=(4+1)//2=2
    assert ok == (positives >= 2)
    # 空伙伴 → 放行且无分数
    ok2, scores2 = cross_validate_tokens([0], [], "1d", 0.0003)
    assert ok2 and not scores2


def test_dedup_top_records_cross_scores():
    """通过严格筛的冠军 metrics 携带 cross_symbol 得分明细"""
    bars = _make_bars(600, 11)
    train, test = bars[:420], bars[420:]
    from app.services.factor_lab.scoring.evaluate import evaluate_factor

    mat_close = np.array([b["close"] for b in train], dtype=float)
    champs = []
    for feat in (0, 1, 2):
        m = evaluate_factor(
            np.asarray(
                __import__("app.services.factor_lab.features", fromlist=["feature_matrix"]).feature_matrix(train)[feat]
            ),
            mat_close,
            cost=0.0005,
            periods=243,
        )
        champs.append((float(m["composite"]), [feat], m))
    peers = [("hc", _make_bars(400, 21)), ("i", _make_bars(400, 22))]
    out = _dedup_top(
        list(champs),
        top_n=5,
        test_bars=test,
        timeframe="1d",
        cost=0.0005,
        use_test=True,
        walk_forward_folds=0,
        train_bars=train,
        trials=1200,
        cross_peers=peers,
    )
    strict = [c for c in out if not (c.metrics or {}).get("overfit_warning")]
    for c in strict:
        assert "cross_symbol" in c.metrics
        assert set(c.metrics["cross_symbol"]) == {"hc", "i"}
