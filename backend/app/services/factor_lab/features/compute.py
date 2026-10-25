"""特征组合 —— compute_features 与 FEATURE_NAMES（append-only）

自单文件 features.py 拆出（2026-08-31，行为零变化）。
所有特征严格因果（只用当前及历史 bar），无未来函数。
价量类特征经因果 zscore 归一化并 clip[-5,5]；
时间类特征本身已在 [-1,1] 且带日内周期语义，不做 zscore
（200 根窗口在 1m 上不足一个交易日，zscore 会把日内周期抹平）。

特征顺序由 FEATURE_NAMES 固定，VM 据此索引。新增特征只能追加在末尾，
且不会影响算子 token（特征 id 空间已冻结，见 token_encoding.py）。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..ops import delta, ts_corr, ts_max, ts_mean, ts_min, ts_std
from .aux_sources import AUX_FEATURES, AUX_GROUPS, aux_groups_present
from .calendar import _dow_dom, _is_night, _time_of_day
from .primitives import (
    _ac1,
    _atr,
    _open_interest,
    _ret,
    _rolling_moment,
    _rsi,
    _to_arr,
    _zscore_causal,
    zscore_window,
)
from .strength import strength_series


def _streak(close: np.ndarray, cap: int = 12) -> np.ndarray:
    """连续同向收盘计数(路径依赖):方向 × min(连涨/连跌根数, cap) / cap

    需要跨根状态累积,无法由现有算子组合表达;逐根单遍 O(n),
    与 _ac1/_open_interest 同属特征层的逐 bar 循环(仅计算一次并缓存)。
    """
    n = len(close)
    out = np.zeros(n, dtype=float)
    run_sign = 0.0
    run_len = 0
    for i in range(1, n):
        if close[i] > close[i - 1]:
            s = 1.0
        elif close[i] < close[i - 1]:
            s = -1.0
        else:
            s = 0.0
        if s == 0.0:
            run_len = 0
        elif s == run_sign:
            run_len += 1
        else:
            run_sign = s
            run_len = 1
        out[i] = run_sign * min(run_len, cap)
    return out / cap


def _vwap_dev(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, w: int = 20
) -> np.ndarray:
    """收盘对量加权典型价均线的偏离(算子库无加权均价,无法组合表达)"""
    tp = (high + low + close) / 3.0
    vwap = ts_mean(tp * volume, w) / np.maximum(ts_mean(volume, w), 1e-9)
    return (close - vwap) / np.maximum(np.abs(vwap), 1e-9)


def _updown_vol_ratio(close: np.ndarray, w: int = 20) -> np.ndarray:
    """上行/下行收益波动占比:(E[up²]−E[dn²])/(E[up²]+E[dn²]) ∈ [−1,1]

    条件统计——把波动按涨跌方向分桶,现有算子无条件分流能力;
    正值 = 涨时波动占优,负值 = 跌时波动占优(波动不对称性)。
    """
    r = _ret(close, 1)
    up = np.where(r > 0, r, 0.0)
    dn = np.where(r < 0, -r, 0.0)
    su = ts_mean(up * up, w)
    sd = ts_mean(dn * dn, w)
    return (su - sd) / np.maximum(su + sd, 1e-12)


def _chan_pos(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, w: int = 20
) -> np.ndarray:
    """收盘在 w 根高低通道内的位置,映射 [−1,1](下轨 −1 / 上轨 +1)

    MIN/MAX+DIV 组合在语法上可达但需 7+ token 且深度易超限,
    作为特征直接提供高频组合的高性价比入口。
    """
    hh = ts_max(high, w)
    ll = ts_min(low, w)
    return (close - ll) / np.maximum(hh - ll, 1e-9) * 2.0 - 1.0


def compute_features(bars: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    """计算全部特征，返回 name -> [T] 因果归一化数组"""
    close = _to_arr(bars, "close")
    high = _to_arr(bars, "high")
    low = _to_arr(bars, "low")
    open_ = _to_arr(bars, "open")
    volume = _to_arr(bars, "volume").astype(float)
    open_interest, has_oi = _open_interest(bars)

    ma20 = ts_mean(close, 20)
    std20 = ts_std(close, 20)
    ma60 = ts_mean(close, 60)
    std60 = ts_std(close, 60)
    vol_ma = ts_mean(volume, 20)
    vol_std = ts_std(volume, 20)
    zeros = np.zeros_like(close)
    oi_delta = delta(open_interest, 1)
    rng = np.maximum(high - low, 1e-9)

    gap = np.zeros_like(close)
    gap[1:] = (open_[1:] - close[:-1]) / np.maximum(np.abs(close[:-1]), 1e-9)

    raw: dict[str, np.ndarray] = {
        # 趋势
        "RET": _ret(close, 1),
        "RET5": _ret(close, 5),
        "RET20": _ret(close, 20),
        "MA_DIFF": (close - ma20) / np.maximum(np.abs(ma20), 1e-9),
        "SLOPE20": delta(ma20, 5) / np.maximum(np.abs(ma20), 1e-9),
        # 波动
        "ATR14": _atr(high, low, close, 14) / np.maximum(close, 1e-9),
        "RVOL": vol_std / np.maximum(vol_ma, 1e-9),
        "HL_RANGE": (high - low) / np.maximum(close, 1e-9),
        # 反转
        "DEV": (close - ma20) / np.maximum(std20, 1e-9),
        "RSI14": _rsi(close, 14),
        "AC1": _ac1(close, 20),
        # 量能
        "VOL_RATIO": volume / np.maximum(vol_ma, 1e-9),
        "VOL_Z": (volume - vol_ma) / np.maximum(vol_std, 1e-9),
        "PV_CORR": ts_corr(_ret(close, 1), volume, 20),
        # 持仓量（期货独有）—— 缺持仓量数据时整体置零，不造假信号
        "OI_CHG": (
            oi_delta / np.maximum(np.abs(open_interest), 1.0) if has_oi else zeros
        ),
        "OI_PV": (
            np.sign(_ret(close, 1)) * np.sign(oi_delta) if has_oi else zeros
        ),
        "VOL_OI": (
            volume / np.maximum(open_interest, 1.0) if has_oi else zeros
        ),
        # ── 扩容批次1（append-only，id 从 19 起；旧 token 语义不可变）──
        # K 线微观结构（首次使用 open 字段：跳空/影线/实体/收盘位置）
        "GAP": gap,
        "CLOSE_POS": (close - low) / rng,
        "UPPER_SHADOW": (high - np.maximum(open_, close)) / np.maximum(close, 1e-9),
        "LOWER_SHADOW": (np.minimum(open_, close) - low) / np.maximum(close, 1e-9),
        "BODY": (close - open_) / np.maximum(close, 1e-9),
        # 长窗口结构（算子窗口上限 20，多周期只能由特征层提供）
        "RET60": _ret(close, 60),
        "MA_DIFF60": (close - ma60) / np.maximum(np.abs(ma60), 1e-9),
        "VOLAT_RATIO": std20 / np.maximum(std60, 1e-9),
        # ── 扩容批次2（id 27 起）：量仓相关 / 高阶矩 / 日历 ──
        "OI_PC": (
            ts_corr(_ret(close, 1), oi_delta, 20) if has_oi else zeros
        ),
        "OI_CHG5": (
            delta(open_interest, 5) / np.maximum(np.abs(open_interest), 1.0)
            if has_oi
            else zeros
        ),
        "OI_CHG20": (
            delta(open_interest, 20) / np.maximum(np.abs(open_interest), 1.0)
            if has_oi
            else zeros
        ),
        "VOL_OI_MA": (
            ts_mean(volume / np.maximum(open_interest, 1.0), 20)
            if has_oi
            else zeros
        ),
        "SKEW20": _rolling_moment(_ret(close, 1), 20, 3),
        "KURT20": _rolling_moment(_ret(close, 1), 20, 4),
        # ── 扩容批次3（id 35）：强弱值（图表强弱指标同口径，默认参数）──
        "STRENGTH": strength_series(bars),
        # ── 桌面端本地批次(id 36-39):2026-09-28 服务端同步,token 两端互认 ──
        "STREAK": _streak(close),
        "VWAP_DEV": _vwap_dev(high, low, close, volume),
        "UPDOWN_VOL_RATIO": _updown_vol_ratio(close),
        "CHAN_POS": _chan_pos(high, low, close),
    }
    # 价量类做因果归一化（窗口至少覆盖一个完整交易日）
    window = zscore_window(bars)
    out = {name: _zscore_causal(arr, window) for name, arr in raw.items()}
    # 时间类已在 [-1,1] 且带日内周期语义，zscore 会把周期抹平，故不归一化
    out["TOD"] = _time_of_day(bars)
    out["NIGHT"] = _is_night(bars)
    # 日历类同理：周期语义值，zscore 会抹平周内效应
    dow, dom = _dow_dom(bars)
    out["DOW"] = dow
    out["DOM"] = dom
    # 扩展信息源(id 40 起):仅 bars 带 x_* 字段(aux_sources.attach_*)时计算;
    # 未启用的组置零(与缺持仓量同理,不造假信号,且被 feature_pool 排除出终端集)
    present = set(aux_groups_present(bars))
    if present:
        zeros = np.zeros(len(bars), dtype=float)
        for name, key, group in AUX_FEATURES:
            out[name] = (
                _zscore_causal(_aux_series(bars, key), window) if group in present else zeros
            )
    return out


def _aux_series(bars: list[dict[str, Any]], key: str) -> np.ndarray:
    """扩展信息源原始值:None 前向填充(因果),首个有效值之前为 0"""
    out = np.zeros(len(bars), dtype=float)
    last = 0.0
    for i, bar in enumerate(bars):
        v = bar.get(key)
        if v is not None:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                fv = float("nan")
            if math.isfinite(fv):
                last = fv
        out[i] = last
    return out


FEATURE_NAMES: tuple[str, ...] = (
    "RET",
    "RET5",
    "RET20",
    "MA_DIFF",
    "SLOPE20",
    "ATR14",
    "RVOL",
    "HL_RANGE",
    "DEV",
    "RSI14",
    "AC1",
    "VOL_RATIO",
    "VOL_Z",
    "PV_CORR",
    # 以下为期货特有特征，只能追加在末尾（已有 token 的特征 id 不可变动）
    "OI_CHG",
    "OI_PV",
    "VOL_OI",
    "TOD",
    "NIGHT",
    # ── 扩容批次1（id 19-26）：K 线微观结构 + 长窗口 ──
    "GAP",
    "CLOSE_POS",
    "UPPER_SHADOW",
    "LOWER_SHADOW",
    "BODY",
    "RET60",
    "MA_DIFF60",
    "VOLAT_RATIO",
    # ── 扩容批次2（id 27-34）：量仓相关 / 高阶矩 / 日历 ──
    "OI_PC",
    "OI_CHG5",
    "OI_CHG20",
    "VOL_OI_MA",
    "SKEW20",
    "KURT20",
    "DOW",
    "DOM",
    # ── 扩容批次3（id 35）：强弱值 ──
    "STRENGTH",
    # ── 桌面端本地批次(id 36-39):2026-09-28 服务端同步,token 两端互认 ──
    "STREAK",
    "VWAP_DEV",
    "UPDOWN_VOL_RATIO",
    "CHAN_POS",
    # ── 桌面端扩展信息源(id 40-58):bars 带 x_* 字段(aux_sources.attach_*)才
    #    参与(feature_matrix 动态行数);定义单一来源见 aux_sources.AUX_FEATURES ──
) + tuple(name for name, _, _ in AUX_FEATURES)

# 基础特征数:bars 不带扩展信息源时 feature_matrix 只出这么多行,
# 与扩展前逐位一致(GP 的 feat_n、RNG 轨迹都不变)
BASE_FEAT_COUNT = FEATURE_NAMES.index("CHAN_POS") + 1
