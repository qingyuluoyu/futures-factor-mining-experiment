"""因子特征工程包 —— 自单文件 features.py 拆分（2026-08-31，行为零变化）

结构：
- primitives.py  数值原语（收益/ATR/RSI/自相关/因果 zscore/滚动矩/持仓量提取）
- calendar.py    日内/日历特征（时钟进度/夜盘/周内日/月内位置）
- compute.py     compute_features + FEATURE_NAMES（特征清单 append-only）
- matrix.py      特征矩阵按段缓存（bars_signature / feature_matrix）

本 __init__ re-export 全部原有公开符号（含测试引用的私有函数与缓存对象），
所有调用方 `from app.services.factor_lab.features import X` 路径不变。
新增强弱值特征见 strength.py（第 9 步加入）。
"""

from .aux_sources import AUX_FEATURES, AUX_GROUPS  # noqa: F401 —— aux 特征单一来源
from .calendar import _is_night, _time_of_day  # noqa: F401 —— 测试按旧路径导入
from .compute import BASE_FEAT_COUNT, FEATURE_NAMES, compute_features
from .matrix import (  # noqa: F401 —— _MATRIX_CACHE 须与本包同名同对象
    _MATRIX_CACHE,
    bars_signature,
    clear_feature_matrix_cache,
    feature_matrix,
    feature_pool,
)
from .primitives import _open_interest, _ret, zscore_window  # noqa: F401

__all__ = [
    "AUX_FEATURES",
    "AUX_GROUPS",
    "BASE_FEAT_COUNT",
    "FEATURE_NAMES",
    "bars_signature",
    "clear_feature_matrix_cache",
    "compute_features",
    "feature_matrix",
    "feature_pool",
    "zscore_window",
]
