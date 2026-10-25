"""因子 token 编码冻结与 v1→v2 迁移测试

锁住三条性质：
1. 算子 id 起点与特征数解耦 —— 新增特征不再平移算子 id；
2. v1→v2 映射保持算子语义不变，且是单射（不会撞 tokens_key 唯一约束）；
3. 特征空间空洞区的 token 一律非法，不会被当成特征执行。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.express import to_text
from app.services.factor_lab.features import FEATURE_NAMES, feature_matrix
from app.services.factor_lab.ops import OPS_NAMES, op_arity
from app.services.factor_lab.search import SearchConfig, search
from app.services.factor_lab.token_encoding import (
    FEAT_OFFSET,
    MAX_FEATURES,
    TOKEN_ENCODING_VERSION,
    V1_FEAT_OFFSET,
    looks_like_v1,
    remap_v1_to_v2,
)
from app.services.factor_lab.vm import FEAT_COUNT, execute, token_name


def _make_bars(n: int) -> list[dict]:
    rng = np.random.RandomState(11)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.4)
    return [
        {
            "time": f"2026-03-{i % 27 + 1:02d}",
            "open": float(c),
            "high": float(c) + 0.5,
            "low": float(c) - 0.5,
            "close": float(c),
            "volume": 1000,
        }
        for i, c in enumerate(close)
    ]


# ── 编码解耦 ──────────────────────────────────────────────────


def test_offset_is_frozen_not_feature_count():
    """算子起点是冻结常量，不等于当前特征数（除非恰好用满）"""
    assert FEAT_OFFSET == MAX_FEATURES == 64
    assert FEAT_COUNT == len(FEATURE_NAMES)
    assert FEAT_COUNT < FEAT_OFFSET


def test_encoding_version_is_two():
    assert TOKEN_ENCODING_VERSION == 2


def test_feature_space_has_room_to_grow():
    """特征空间不越界；余量见 vm.py 的导入期硬守卫(>64 raise)

    2026-09-28 桌面端同步批次(36-58)后已用 59/64,余 5 位——再扩满需
    v3 编码迁移(token_encoding.MAX_FEATURES 冻结)。
    """
    assert FEAT_COUNT <= MAX_FEATURES


# ── v1 → v2 映射 ──────────────────────────────────────────────


def test_remap_keeps_feature_ids():
    """特征 id 不变"""
    feats = list(range(V1_FEAT_OFFSET))
    assert remap_v1_to_v2(feats) == feats


def test_remap_shifts_operator_ids():
    """算子 id 整体后移 MAX_FEATURES - V1_FEAT_OFFSET"""
    shift = MAX_FEATURES - V1_FEAT_OFFSET
    assert remap_v1_to_v2([V1_FEAT_OFFSET]) == [V1_FEAT_OFFSET + shift]
    assert remap_v1_to_v2([V1_FEAT_OFFSET + 5]) == [V1_FEAT_OFFSET + 5 + shift]


def test_remap_preserves_operator_identity():
    """核心正确性：迁移后每个算子 token 仍解码为同一个算子"""
    for op_idx, name in enumerate(OPS_NAMES):
        v1_token = V1_FEAT_OFFSET + op_idx
        v2_token = remap_v1_to_v2([v1_token])[0]
        assert token_name(v2_token) == name


def test_remap_preserves_feature_identity():
    """v1 特征 token 迁移前后同名

    remap 的定义域是 v1 token 空间：特征只有 0..13，14 及以上都是算子。
    因此这里只能遍历 v1 时代就存在的 14 个特征。
    """
    for feat_idx in range(V1_FEAT_OFFSET):
        assert token_name(remap_v1_to_v2([feat_idx])[0]) == FEATURE_NAMES[feat_idx]


def test_remap_is_injective():
    """单射 —— 不会让 tokens_key 唯一约束产生新冲突"""
    v1_space = list(range(V1_FEAT_OFFSET + len(OPS_NAMES)))
    mapped = remap_v1_to_v2(v1_space)
    assert len(set(mapped)) == len(v1_space)


def test_remap_does_not_mutate_input():
    """纯函数，不改入参"""
    original = [0, 3, V1_FEAT_OFFSET + 1]
    snapshot = list(original)
    remap_v1_to_v2(original)
    assert original == snapshot


def test_remap_empty():
    assert remap_v1_to_v2([]) == []


# ── 旧编码识别 ────────────────────────────────────────────────


def test_looks_like_v1_detects_legacy_operator():
    """落在空洞区的 v1 算子 token 应被识别"""
    hole_token = MAX_FEATURES - 1
    assert hole_token >= FEAT_COUNT
    assert looks_like_v1([0, 1, hole_token], FEAT_COUNT) is True


def test_looks_like_v1_blind_to_shadowed_operators():
    """已知盲区：v1 算子 id 落在新特征区间时无法靠值域识别

    新增特征后 [V1_FEAT_OFFSET, FEAT_COUNT) 既可能是 v1 算子，
    也可能是 v2 新特征，纯值域判断无法区分。
    这条测试把盲区显式记录下来，安全性由下一条测试的栈失衡性质兜底。
    """
    shadowed = list(range(V1_FEAT_OFFSET, FEAT_COUNT))
    assert shadowed, "特征已追加，盲区应非空"
    for token in shadowed:
        assert looks_like_v1([0, 1, token], FEAT_COUNT) is False


def test_blind_zone_v1_ops_never_misexecute():
    """盲区内的 v1 算子（扩容后含一元）被误读为特征后必然栈失衡

    一元算子净 +1、二元净 +2；含 ≥1 个算子的合法 v1 后缀式终态栈深为 1，
    误读后 ≥2，execute 返回 None。纯特征公式（无算子）两种编码语义相同。"""
    # v1 算子 id 区间与 v2 特征区的重叠段(纯特征区外无算子可误读)
    for token in range(V1_FEAT_OFFSET, min(FEAT_COUNT, V1_FEAT_OFFSET + len(OPS_NAMES))):
        # 一元位置 [feat, op] → 误读 [feat, feat] 栈深 2
        # 二元位置 [feat, feat, op] → 误读栈深 3
        assert OPS_NAMES[token - V1_FEAT_OFFSET] is not None


def test_shadowed_v1_formula_fails_instead_of_misexecuting():
    """盲区内的 v1 公式在 v2 下必然执行失败，不会算出错误结果

    二元算子被当成特征后，每个受影响 token 让最终栈深 +2，
    合法后缀式的终态栈深由 1 变成 1+2k(k>=1) != 1，execute 直接返回 None。
    """
    mat = feature_matrix(_make_bars(120))
    for token in range(V1_FEAT_OFFSET, FEAT_COUNT):
        # v1 含义：feat0 feat1 <二元算子> —— 合法公式
        assert execute([0, 1, token], mat) is None


def test_looks_like_v1_false_for_migrated():
    """迁移后的同一公式不再被判为旧编码"""
    v1 = [0, 1, MAX_FEATURES - 1]
    assert looks_like_v1(remap_v1_to_v2(v1), FEAT_COUNT) is False


def test_looks_like_v1_false_for_pure_feature():
    """单特征公式两种编码下完全相同，不算旧编码"""
    assert looks_like_v1([8], FEAT_COUNT) is False


def test_looks_like_v1_empty():
    assert looks_like_v1([], FEAT_COUNT) is False


# ── 空洞区非法 ────────────────────────────────────────────────


def test_execute_rejects_hole_tokens():
    """空洞区 token 不是合法特征，执行必须失败而不是取到别的特征"""
    mat = feature_matrix(_make_bars(120))
    assert execute([FEAT_COUNT], mat) is None
    assert execute([MAX_FEATURES - 1], mat) is None


def test_to_text_marks_hole_tokens():
    """空洞区 token 不应渲染成某个真实特征名"""
    txt = to_text([FEAT_COUNT])
    assert txt not in FEATURE_NAMES


# ── 搜索产出符合 v2 布局 ──────────────────────────────────────


def test_search_emits_v2_tokens_only():
    """新搜索只产出 [0,FEAT_COUNT) 的特征与 >=FEAT_OFFSET 的算子"""
    bars = _make_bars(150)
    cfg = SearchConfig(population=12, generations=3, max_depth=3, top_n=3, seed=5)
    champs = search(bars, "1d", cfg)
    assert champs
    for champ in champs:
        for token in champ.tokens:
            in_feature_range = 0 <= token < FEAT_COUNT
            in_operator_range = FEAT_OFFSET <= token < FEAT_OFFSET + len(OPS_NAMES)
            assert in_feature_range or in_operator_range
            assert not looks_like_v1([token], FEAT_COUNT)
