"""P2 搜索升级测试 —— tokens↔tree 往返、shortlist 相关性去重"""

from __future__ import annotations

import random

import numpy as np

from app.services.factor_lab import feature_matrix
from app.services.factor_lab.ops import OPS_CONFIG
from app.services.factor_lab.search import (
    SearchConfig,
    _random_tree,
    search,
    tokens_to_tree,
    tree_to_tokens,
)
from app.services.factor_lab.vm import FEAT_COUNT


def _make_bars(n: int = 200, seed: int = 0) -> list[dict]:
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


def test_tokens_tree_roundtrip():
    """随机树 → tokens → tree 完全还原（种子进基因池的前提）"""
    rng = random.Random(42)
    op_one = [i for i, (_, _, a) in enumerate(OPS_CONFIG) if a == 1]
    op_two = [i for i, (_, _, a) in enumerate(OPS_CONFIG) if a == 2]
    for _ in range(200):
        tree = _random_tree(4, FEAT_COUNT, op_one, op_two, rng)
        tokens = tree_to_tokens(tree)
        back = tokens_to_tree(tokens)
        assert back is not None
        assert tree_to_tokens(back) == tokens


def test_tokens_to_tree_rejects_garbage():
    assert tokens_to_tree([]) is None
    assert tokens_to_tree([FEAT_COUNT + len(OPS_CONFIG) + 5]) is None
    # 二元算子缺操作数
    assert tokens_to_tree([0, FEAT_COUNT + 0]) is None  # ADD 只有一个操作数


def test_shortlist_correlation_dedup():
    """高度相关的因子只留最优一个（|corr|>0.9 视为同一因子）"""

    bars = _make_bars(300, seed=9)
    mat = feature_matrix(bars)
    from app.services.factor_lab import execute

    # 两个几乎相同的因子：单特征 vs 该特征加极小扰动
    f0 = execute([0], mat)
    f1 = execute([0], mat) * 1.0001
    assert f0 is not None and f1 is not None
    corr = np.corrcoef(f0, f1)[0, 1]
    assert corr > 0.99

    # 构造 best_seen：两个高相关候选（RET 与 NEG(RET)，corr=-1）+ 一个低相关候选
    from app.services.factor_lab.scoring.evaluate import evaluate_factor

    close = np.array([b["close"] for b in bars], dtype=float)
    m0 = evaluate_factor(f0, close, cost=0.0005, periods=243)
    f1 = execute([0, 64 + 7], mat)  # NEG(RET)：与 RET 完全反相关
    assert f1 is not None
    m1 = evaluate_factor(f1, close, cost=0.0005, periods=243)
    f2 = execute([1], mat)  # RET5，与 RET 相关性有限
    assert f2 is not None
    m2 = evaluate_factor(f2, close, cost=0.0005, periods=243)

    from app.services.factor_lab.search import _dedup_top

    champs_in = [
        (float(m0["composite"]) + 0.01, [0], m0),
        (float(m1["composite"]), [0, 64 + 7], m1),
        (float(m2["composite"]) - 0.01, [1], m2),
    ]
    out = _dedup_top(
        champs_in,
        top_n=5,
        timeframe="1d",
        cost=0.0005,
        use_test=False,
        walk_forward_folds=0,
        train_bars=bars,
    )
    tokens_out = [tuple(c.tokens) for c in out]
    # NEG(RET) 与 RET |corr|=1 被去重；RET 与 RET5 都保留
    assert (0,) in tokens_out
    assert (1,) in tokens_out
    assert len(out) == 2


def test_search_with_seeds_in_gene_pool_smoke():
    """种子进基因池后搜索仍正常运行（smoke）"""
    bars = _make_bars(200, seed=5)
    champs = search(
        bars,
        "1d",
        SearchConfig(
            population=12, generations=3, top_n=3, seed_tokens=[[0], [1, 2]]
        ),
    )
    assert isinstance(champs, list)
