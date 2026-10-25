"""特征矩阵与按段缓存 —— bars_signature / feature_matrix / LRU 预算

自单文件 features.py 拆出（2026-08-31，行为零变化）。

── 特征矩阵按段缓存 ──────────────────────────────────────────
feature_matrix 只取决于 bars，但调用方按「候选因子 × 段」组织循环，
同一个段会被不同 tokens 反复重算。walk_forward 的段级缓存键含 tokens，
拦不住这层重复：实测一次超级因子挖掘任务（pop=40 × gen=30、2000 根日线）
调用 feature_matrix 330 次而只有 12 个不同段（27.5× 冗余），且它占单次
冷段评估耗时的 99%。按段缓存后 search_stepwise 由 49.5s 降到 1.1s，
冠军 tokens 与 composite 逐位一致——纯计算复用，无语义变化。

上限按元素数而非条目数计：段长跨度大（日线数千根、walk-forward 各折
训练段逐折增长），条目数上限约束不住内存。4M 元素 ≈ 32MB float64——
实测一次 2000 根日线挖掘只占 12 条 / 24.9 万元素（约 2MB），预算只在
超深数据上才收紧；上限取保守值是因为同一份内核也跑在桌面端 Pyodide 里
（浏览器堆更紧，且缓存驻留与该处的代际回收诉求相抵）。

实盘因子策略（ai_trading）每 2 秒按最新 bars 求一次信号，末根一变签名
即变，基本恒为未命中——它只付一次字典查找的代价，并会挤占缓存。挖掘
侧不受影响：挖掘每代都重复命中自己那十几个段，LRU 次序被持续刷新，
驻留优先级高于只写一次的实盘条目；最坏情况也只是 32MB 上限内的浪费。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import numpy as np

from .aux_sources import AUX_FEATURES, AUX_GROUPS, aux_groups_present
from .compute import BASE_FEAT_COUNT, FEATURE_NAMES, compute_features

_MATRIX_CACHE: OrderedDict[tuple, np.ndarray] = OrderedDict()
_MATRIX_CACHE_MAX_ELEMENTS = 4_000_000


def bars_signature(bars: list[dict[str, Any]]) -> tuple:
    """段签名 —— 按段缓存的键

    取首/中/尾三根 bar 的时间与收盘加长度：同一段必同签名；三点采样
    使不同品种或不同区间偶然同签名的概率可忽略（仅取首尾时，同长度
    同起止日的两个品种只靠一个收盘价区分）。
    """
    if not bars:
        return (0,)
    mid = len(bars) // 2
    sig = (
        len(bars),
        str(bars[0].get("time") or ""),
        str(bars[0].get("close") or ""),
        str(bars[mid].get("time") or ""),
        str(bars[mid].get("close") or ""),
        str(bars[-1].get("time") or ""),
        str(bars[-1].get("close") or ""),
    )
    # 扩展信息源改变特征矩阵(行数与取值),必须进签名:同一段 K 线带/不带
    # 信息源、或挂了不同信息源时不得命中同一缓存。无扩展时签名与原来一致。
    present = aux_groups_present(bars)
    if not present:
        return sig
    probe = tuple(
        str(bars[i].get(AUX_GROUPS[g][0]))
        for g in present
        for i in (0, mid, len(bars) - 1)
    )
    return sig + (present, probe)


def feature_pool(bars: list[dict[str, Any]], feat_mat: np.ndarray) -> int | tuple[int, ...]:
    """GP 终端(特征)抽样池

    无扩展信息源 → 返回行数 int(原 rng.randrange(feat_n) 路径,逐位不变);
    有 → 返回可用特征 id 元组:基础特征 + 已启用组(未启用组的全零行不参与抽样)。
    """
    present = set(aux_groups_present(bars))
    if not present:
        return int(feat_mat.shape[0])
    ids = list(range(BASE_FEAT_COUNT))
    for offset, (_, _, group) in enumerate(AUX_FEATURES):
        if group in present:
            ids.append(BASE_FEAT_COUNT + offset)
    return tuple(ids)


def _evict_matrix_cache() -> None:
    """按元素预算淘汰最久未用的段（至少保留刚写入的一条）"""
    total = sum(int(m.size) for m in _MATRIX_CACHE.values())
    while len(_MATRIX_CACHE) > 1 and total > _MATRIX_CACHE_MAX_ELEMENTS:
        _, dropped = _MATRIX_CACHE.popitem(last=False)
        total -= int(dropped.size)


def clear_feature_matrix_cache() -> None:
    """清空特征矩阵缓存（测试隔离用）"""
    _MATRIX_CACHE.clear()


def feature_matrix(bars: list[dict[str, Any]]) -> np.ndarray:
    """返回 [F, T] 特征矩阵，顺序与 FEATURE_NAMES 一致

    bars 不带扩展信息源时只含前 BASE_FEAT_COUNT 行(与扩展前逐位一致);
    含扩展 token 的公式在这种矩阵上执行会越界 → execute 返回 None(无信号),
    不会用错数据。

    结果按段缓存，返回只读数组（调用方只按行取特征送入 StackVM，
    不写入矩阵；只读标记把将来的意外写入变成显式报错而非静默串数据）。
    """
    key = bars_signature(bars)
    hit = _MATRIX_CACHE.get(key)
    if hit is not None:
        _MATRIX_CACHE.move_to_end(key)
        return hit
    feats = compute_features(bars)
    names = FEATURE_NAMES if aux_groups_present(bars) else FEATURE_NAMES[:BASE_FEAT_COUNT]
    matrix = np.vstack([feats[name] for name in names])
    matrix.flags.writeable = False
    _MATRIX_CACHE[key] = matrix
    _MATRIX_CACHE.move_to_end(key)
    _evict_matrix_cache()
    return matrix
