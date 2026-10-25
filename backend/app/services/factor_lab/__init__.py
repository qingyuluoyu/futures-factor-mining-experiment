"""因子挖掘内核 —— 特征、算子、StackVM、表达式

纯函数、无 torch、无外部依赖（仅 numpy）。
"""

from __future__ import annotations

from .express import to_text
from .features import (
    FEATURE_NAMES,
    bars_signature,
    clear_feature_matrix_cache,
    compute_features,
    feature_matrix,
)
from .ops import OPS_CONFIG, OPS_NAMES, OP_INDEX, op_arity
from .vm import (
    FEAT_COUNT,
    FEAT_OFFSET,
    POSITIVE_ONLY_OPS,
    execute,
    is_constant,
    validate,
)

__all__ = [
    "FEATURE_NAMES",
    "bars_signature",
    "clear_feature_matrix_cache",
    "compute_features",
    "feature_matrix",
    "OPS_CONFIG",
    "OPS_NAMES",
    "OP_INDEX",
    "op_arity",
    "FEAT_COUNT",
    "FEAT_OFFSET",
    "POSITIVE_ONLY_OPS",
    "execute",
    "validate",
    "is_constant",
    "to_text",
    "kernel_version",
]


def kernel_version() -> str:
    """因子内核口径版本——同因子在新旧口径下指标不同，历史/收藏按此戳区分。

    批次三口径变更（P0-2 因果滚动归一化窗 250 / P1-7 warmup 切片评估 +
    MIN_TEST_BARS=120，2026-09-28 与桌面端 .3 对齐）：同因子仓位序列与
    指标均会变化（.2 全样本归一化构成全局前视，实盘不可复现）。同时
    特征集扩至 59（桌面端本地批次 36-39 + 扩展信息源 40-58 定义同步，
    append-only 不改旧 token 语义）。P1-4（OOS 负改加性罚分）维持不采纳
    （E-Z1 证伪，×0.0 零平台）。与桌面端 factor_local.kernel_version()
    同值同步；桌面端 selection_v2/evolve_v2 等本地增强默认关，不影响数值。
    """
    return "pykernel-factor-2026-09-01.3"
