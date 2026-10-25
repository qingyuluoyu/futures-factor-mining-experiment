"""特征矩阵按段缓存 —— 正确性、隔离性与内存边界

缓存是纯计算复用：同一段的 feature_matrix 结果与未缓存时逐位一致，
搜索冠军不受影响。本组测试守住三件事：
1. 命中/未命中的判定正确（同段命中、不同段必不串）；
2. 缓存矩阵只读，防将来有人就地改写污染其他调用方；
3. 元素预算生效，超深数据下不会无界增长。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.services.factor_lab import features as F
from app.services.factor_lab.features import (
    bars_signature,
    clear_feature_matrix_cache,
    compute_features,
    feature_matrix,
    FEATURE_NAMES,
)


def _bars(n: int, seed: int, start_price: float = 100.0) -> list[dict]:
    """构造 n 根合成日线（确定性，仅供数值对照）"""
    rng = np.random.default_rng(seed)
    close = start_price * np.exp(np.cumsum(rng.normal(0, 0.011, n)))
    day = dt.datetime(2019, 1, 1)
    out: list[dict] = []
    for i in range(n):
        c = float(close[i])
        o = c * (1 + float(rng.normal(0, 0.002)))
        out.append(
            {
                "time": (day + dt.timedelta(days=i)).strftime("%Y-%m-%d"),
                "open": o,
                "high": max(o, c) * 1.004,
                "low": min(o, c) * 0.996,
                "close": c,
                "volume": 100000.0,
                "open_interest": 200000.0,
            }
        )
    return out


@pytest.fixture(autouse=True)
def _isolate_cache():
    """每个用例前后都清缓存，避免跨用例串状态"""
    clear_feature_matrix_cache()
    yield
    clear_feature_matrix_cache()


def test_cache_hit_returns_same_object() -> None:
    """同一段第二次调用命中缓存（返回同一对象，不重算）"""
    bars = _bars(200, seed=1)
    first = feature_matrix(bars)
    second = feature_matrix(list(bars))  # 新 list、同内容 → 同签名
    assert second is first
    assert len(F._MATRIX_CACHE) == 1


def test_cached_matrix_equals_uncached_computation() -> None:
    """缓存值与直接由 compute_features 拼出的矩阵逐位一致"""
    bars = _bars(300, seed=2)
    cached = feature_matrix(bars)
    feats = compute_features(bars)
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    expected = np.vstack([feats[name] for name in FEATURE_NAMES[:BASE_FEAT_COUNT]])
    assert np.array_equal(np.asarray(cached), expected)


def test_distinct_segments_do_not_collide() -> None:
    """不同段（含同长度不同数据）各自独立，不会串矩阵"""
    a = _bars(200, seed=3)
    b = _bars(200, seed=4)  # 同长度、同起止日期，价格不同
    assert bars_signature(a) != bars_signature(b)
    ma = feature_matrix(a)
    mb = feature_matrix(b)
    assert not np.array_equal(np.asarray(ma), np.asarray(mb))
    assert len(F._MATRIX_CACHE) == 2


def test_subsegments_are_separate_entries() -> None:
    """切片段与全段是不同的键（walk-forward 各折依赖这一点）"""
    bars = _bars(400, seed=5)
    full = feature_matrix(bars)
    head = feature_matrix(bars[:200])
    assert full.shape[1] == 400
    assert head.shape[1] == 200
    assert len(F._MATRIX_CACHE) == 2


def test_signature_uses_mid_bar() -> None:
    """签名取首/中/尾三点：只改中间一根也必须换键"""
    bars = _bars(101, seed=6)
    sig_before = bars_signature(bars)
    tweaked = [dict(b) for b in bars]
    tweaked[50]["close"] = float(tweaked[50]["close"]) * 1.5
    assert bars_signature(tweaked) != sig_before


def test_empty_bars_signature() -> None:
    """空段签名不抛错（调用方自行守卫是否求值）"""
    assert bars_signature([]) == (0,)


def test_cached_matrix_is_readonly() -> None:
    """缓存矩阵只读——就地写入必须显式报错而非静默污染"""
    matrix = feature_matrix(_bars(120, seed=7))
    assert matrix.flags.writeable is False
    with pytest.raises(ValueError):
        matrix[0, 0] = 1.0


def test_readonly_matrix_still_usable_by_vm() -> None:
    """只读矩阵不影响 StackVM 执行（算子只读取、不就地改写）"""
    from app.services.factor_lab.vm import FEAT_OFFSET, execute
    from app.services.factor_lab.ops import OP_INDEX

    bars = _bars(200, seed=8)
    matrix = feature_matrix(bars)
    tokens = [0, 3, FEAT_OFFSET + OP_INDEX["SUB"]]  # RET - MA_DIFF
    out = execute(tokens, matrix)
    assert out is not None
    assert out.shape == (len(bars),)
    assert np.isfinite(out).all()


def test_eviction_respects_element_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """超预算时按 LRU 淘汰，且至少保留刚写入的一条"""
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    feature_count = BASE_FEAT_COUNT  # 无扩展信息源时的实际矩阵行数
    # 预算设成刚够 2 段 × 100 根
    monkeypatch.setattr(F.matrix, "_MATRIX_CACHE_MAX_ELEMENTS", feature_count * 250)
    first = _bars(100, seed=10)
    second = _bars(100, seed=11)
    third = _bars(100, seed=12)
    feature_matrix(first)
    feature_matrix(second)
    assert len(F._MATRIX_CACHE) == 2
    feature_matrix(third)  # 触发淘汰
    assert len(F._MATRIX_CACHE) == 2
    assert bars_signature(first) not in F._MATRIX_CACHE  # 最久未用者被淘汰
    assert bars_signature(third) in F._MATRIX_CACHE


def test_oversized_single_segment_still_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单段本身超预算时仍保留它（否则该段每次都要重算）"""
    monkeypatch.setattr(F.matrix, "_MATRIX_CACHE_MAX_ELEMENTS", 1)
    bars = _bars(100, seed=13)
    feature_matrix(bars)
    assert len(F._MATRIX_CACHE) == 1


def test_lru_order_updated_on_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中会刷新 LRU 次序：被复用的段不该先于新段被淘汰"""
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    feature_count = BASE_FEAT_COUNT  # 无扩展信息源时的实际矩阵行数
    monkeypatch.setattr(F.matrix, "_MATRIX_CACHE_MAX_ELEMENTS", feature_count * 250)
    first = _bars(100, seed=20)
    second = _bars(100, seed=21)
    third = _bars(100, seed=22)
    feature_matrix(first)
    feature_matrix(second)
    feature_matrix(first)  # 复用 first → second 成为最久未用
    feature_matrix(third)
    assert bars_signature(second) not in F._MATRIX_CACHE
    assert bars_signature(first) in F._MATRIX_CACHE


# ── 搜索结果不受缓存影响 ──────────────────────────────────────


def _fingerprint(champions: list) -> list[tuple]:
    return [(tuple(c.tokens), round(c.composite, 12)) for c in champions]


def _search_config(seed: int):
    from app.services.factor_lab.search import SearchConfig

    return SearchConfig(
        population=20,
        generations=8,
        train_ratio=0.7,
        walk_forward_folds=2,
        top_n=5,
        seed=seed,
    )


def test_search_result_identical_cold_and_warm() -> None:
    """冷缓存与热缓存跑出的冠军逐位一致（缓存只省算力，不改数值）"""
    from app.services.factor_lab.search import search
    from app.services.factor_lab.scoring.walk_forward import clear_segment_cache

    bars = _bars(600, seed=31)
    clear_feature_matrix_cache()
    clear_segment_cache()
    cold = _fingerprint(search(bars, "1d", _search_config(42)))
    # 不清缓存直接重跑：全程命中
    warm = _fingerprint(search(bars, "1d", _search_config(42)))
    assert cold == warm
    assert cold  # 该配置下确有产出，避免空列表让断言失去意义


def test_stepwise_matches_search() -> None:
    """search() 与 search_stepwise() 末代冠军一致

    两条路径对 feature_matrix 的调用次序与次数完全不同（末代筛一次
    vs 每代筛一次），缓存若返回错段矩阵，这里必然分叉。
    """
    from app.services.factor_lab.search import search, search_stepwise
    from app.services.factor_lab.scoring.walk_forward import clear_segment_cache

    bars = _bars(600, seed=32)
    clear_feature_matrix_cache()
    clear_segment_cache()
    one_shot = _fingerprint(search(bars, "1d", _search_config(7)))

    clear_feature_matrix_cache()
    clear_segment_cache()
    last = None
    for snapshot in search_stepwise(bars, "1d", _search_config(7)):
        last = snapshot
    assert last is not None
    assert _fingerprint(last.champions) == one_shot
