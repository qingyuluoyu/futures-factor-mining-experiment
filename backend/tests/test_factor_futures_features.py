"""期货特有特征测试 —— 持仓量与日内时间

锁住四条性质：
1. 特征只能追加在末尾，已有特征 id 不变（否则存量 token 语义漂移）；
2. 持仓量缺失时仓量类特征置零，不造假信号；
3. 日内时间特征严格因果 —— 不依赖「当日共几根 bar」这类未来信息；
4. 仓量价配合的符号语义正确（价涨仓增 = +1，价涨仓减 = -1）。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.features import (
    FEATURE_NAMES,
    compute_features,
    feature_matrix,
)

# v1 时代已存在的 14 个特征，顺序与 id 必须永久保持
LEGACY_FEATURES = (
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
)


def _bars(n: int, with_oi: bool, start_hour: int) -> list[dict]:
    """合成分钟 bars；with_oi 控制是否带持仓量"""
    rng = np.random.RandomState(7)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.3)
    out: list[dict] = []
    for i, c in enumerate(close):
        minute = (start_hour * 60 + i) % (24 * 60)
        bar = {
            "time": f"2026-03-02 {minute // 60:02d}:{minute % 60:02d}:00",
            "open": float(c),
            "high": float(c) + 0.4,
            "low": float(c) - 0.4,
            "close": float(c),
            "volume": 1000 + i,
        }
        if with_oi:
            bar["open_interest"] = 300000 + i * 10
        out.append(bar)
    return out


# ── 特征 id 稳定性 ────────────────────────────────────────────


def test_legacy_feature_ids_unchanged():
    """新增特征必须追加在末尾，前 14 个 id 与名称不得变动"""
    assert FEATURE_NAMES[: len(LEGACY_FEATURES)] == LEGACY_FEATURES


def test_new_features_appended():
    """期货特有特征已加入且在末尾（其后为扩容批次追加，append-only）"""
    assert FEATURE_NAMES[14:19] == ("OI_CHG", "OI_PV", "VOL_OI", "TOD", "NIGHT")


def test_feature_matrix_covers_all():
    mat = feature_matrix(_bars(120, with_oi=True, start_hour=9))
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    assert mat.shape[0] == BASE_FEAT_COUNT  # 无扩展信息源 → 基础行数
    assert mat.shape[1] == 120
    assert np.all(np.isfinite(mat))


# ── 持仓量缺失降级 ────────────────────────────────────────────


def test_oi_features_zero_when_missing():
    """整段没有持仓量时，仓量类特征恒零，不产生虚假信号"""
    feats = compute_features(_bars(120, with_oi=False, start_hour=9))
    for name in ("OI_CHG", "OI_PV", "VOL_OI"):
        assert np.allclose(feats[name], 0.0), name


def test_oi_features_active_when_present():
    """有持仓量时仓量类特征不再恒零"""
    feats = compute_features(_bars(120, with_oi=True, start_hour=9))
    assert not np.allclose(feats["OI_CHG"], 0.0)
    assert not np.allclose(feats["VOL_OI"], 0.0)


def test_oi_forward_fill_is_causal():
    """持仓量缺口用前值填充，不使用后续 bar"""
    bars = _bars(60, with_oi=True, start_hour=9)
    bars[30]["open_interest"] = None
    feats = compute_features(bars)
    assert np.all(np.isfinite(feats["OI_CHG"]))


# ── 仓量价配合语义 ────────────────────────────────────────────


def test_oi_pv_sign_semantics():
    """价涨仓增 → +1；价涨仓减 → -1（zscore 前的原始符号语义）"""
    from app.services.factor_lab.features import _open_interest, _ret
    from app.services.factor_lab.ops import delta

    up_add = [
        {"close": 100.0, "open_interest": 1000},
        {"close": 101.0, "open_interest": 1100},
    ]
    oi, ok = _open_interest(up_add)
    assert ok
    close = np.array([b["close"] for b in up_add], dtype=float)
    sign = np.sign(_ret(close, 1)) * np.sign(delta(oi, 1))
    assert sign[1] == 1.0

    up_cut = [
        {"close": 100.0, "open_interest": 1000},
        {"close": 101.0, "open_interest": 900},
    ]
    oi2, _ = _open_interest(up_cut)
    close2 = np.array([b["close"] for b in up_cut], dtype=float)
    sign2 = np.sign(_ret(close2, 1)) * np.sign(delta(oi2, 1))
    assert sign2[1] == -1.0


# ── 日内时间特征 ──────────────────────────────────────────────


def test_time_of_day_range_and_anchors():
    """21:00 为 -1，15:00 为 +1，且整体落在 [-1,1]"""
    from app.services.factor_lab.features import _time_of_day

    bars = [
        {"time": "2026-03-02 21:00:00"},
        {"time": "2026-03-03 15:00:00"},
        {"time": "2026-03-03 09:00:00"},
    ]
    tod = _time_of_day(bars)
    assert np.isclose(tod[0], -1.0)
    assert np.isclose(tod[1], 1.0)
    assert -1.0 <= tod[2] <= 1.0


def test_time_of_day_is_causal():
    """同一时刻的取值与序列长度无关 —— 证明没用到「当日共几根」"""
    from app.services.factor_lab.features import _time_of_day

    one = _time_of_day([{"time": "2026-03-03 10:30:00"}])
    many = _time_of_day(
        [{"time": "2026-03-03 10:30:00"}] + [{"time": "2026-03-03 11:00:00"}] * 50
    )
    assert np.isclose(one[0], many[0])


def test_time_of_day_daily_bar_is_zero():
    """日线没有时刻部分，恒为 0"""
    from app.services.factor_lab.features import _time_of_day

    assert np.allclose(_time_of_day([{"time": "2026-03-02"}]), 0.0)


def test_night_flag():
    """夜盘时段标记为 1，日盘为 0"""
    from app.services.factor_lab.features import _is_night

    bars = [
        {"time": "2026-03-02 21:30:00"},
        {"time": "2026-03-03 01:00:00"},
        {"time": "2026-03-03 10:00:00"},
        {"time": "2026-03-03"},
    ]
    night = _is_night(bars)
    assert night[0] == 1.0
    assert night[1] == 1.0
    assert night[2] == 0.0
    assert night[3] == 0.0


def test_time_features_not_zscored():
    """时间特征保持原值域，未被归一化窗口抹平日内周期"""
    feats = compute_features(_bars(300, with_oi=True, start_hour=9))
    assert np.all(feats["TOD"] >= -1.0)
    assert np.all(feats["TOD"] <= 1.0)
    assert set(np.unique(feats["NIGHT"])).issubset({0.0, 1.0})


# ── 归一化窗口 ────────────────────────────────────────────────


def test_zscore_window_daily_stays_base():
    """日线（日均 1 根）沿用基础窗口 200，行为不变"""
    from app.services.factor_lab.features import zscore_window

    bars = [{"time": f"2026-03-{d:02d}"} for d in range(2, 20)]
    assert zscore_window(bars) == 200


def test_zscore_window_covers_full_session_on_1m():
    """1m 日均约 345 根，窗口必须抬到 >= 一个交易日"""
    from app.services.factor_lab.features import zscore_window

    bars: list[dict] = []
    for day in range(2, 6):
        for i in range(345):
            minute = (9 * 60 + i) % (24 * 60)
            bars.append(
                {"time": f"2026-03-{day:02d} {minute // 60:02d}:{minute % 60:02d}:00"}
            )
    window = zscore_window(bars)
    assert window >= 345
    assert window > 200


def test_zscore_window_unchanged_for_coarse_intraday():
    """5m 及以上周期日均 bar 数低于 200，窗口保持 200 不变"""
    from app.services.factor_lab.features import zscore_window

    bars: list[dict] = []
    for day in range(2, 8):
        for i in range(69):
            minute = (9 * 60 + i * 5) % (24 * 60)
            bars.append(
                {"time": f"2026-03-{day:02d} {minute // 60:02d}:{minute % 60:02d}:00"}
            )
    assert zscore_window(bars) == 200


def test_zscore_window_handles_empty():
    from app.services.factor_lab.features import zscore_window

    assert zscore_window([]) == 200
