"""扩容批次2 测试 —— 回归/条件/平滑算子 + 量仓/高阶矩/日历特征 + 组合评估"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab import FEATURE_NAMES, OPS_NAMES, execute
from app.services.factor_lab.features import compute_features, feature_matrix
from app.services.factor_lab.ops import OPS_CONFIG, ts_beta, ts_mean, ts_resid
from app.services.factor_lab.vm import FEAT_COUNT, FEAT_OFFSET, validate
from app.services.factor_lab.scoring.portfolio import evaluate_portfolio

# 批次1 结束时的冻结边界（批次2 只能追加其后）
FROZEN_OPS_34 = (
    "ADD", "SUB", "MUL", "DIV", "MIN", "MAX",
    "ABS", "NEG", "SIGN", "SQRT", "SIGNED_LOG", "SIGMOID", "TANH",
    "TS_MA_5", "TS_MA_10", "TS_MA_20", "TS_STD_10", "TS_STD_20",
    "TS_MAX_10", "TS_MAX_20", "TS_MIN_10", "TS_RANK_10", "TS_RANK_20",
    "TS_ZSCORE_20", "DELTA_1", "DELTA_5", "TS_ATR_NORM",
    "LAG_1", "LAG_5", "CORR_20", "TS_MA_60", "TS_STD_60",
    "TS_ZSCORE_60", "TS_RANK_60",
)
FROZEN_FEATURES_27 = (
    "RET", "RET5", "RET20", "MA_DIFF", "SLOPE20", "ATR14", "RVOL",
    "HL_RANGE", "DEV", "RSI14", "AC1", "VOL_RATIO", "VOL_Z", "PV_CORR",
    "OI_CHG", "OI_PV", "VOL_OI", "TOD", "NIGHT",
    "GAP", "CLOSE_POS", "UPPER_SHADOW", "LOWER_SHADOW", "BODY",
    "RET60", "MA_DIFF60", "VOLAT_RATIO",
)


def _make_bars(n: int = 300, seed: int = 0) -> list[dict]:
    rng = np.random.RandomState(seed)
    close = 3000.0 * np.cumprod(1 + rng.randn(n) * 0.01)
    bars = []
    for i, c in enumerate(close):
        o = c * (1 + rng.randn() * 0.002)
        hi = max(o, c) * (1 + abs(rng.randn()) * 0.003)
        lo = min(o, c) * (1 - abs(rng.randn()) * 0.003)
        bars.append(
            {
                "time": f"2024-{i % 12 + 1:02d}-{i % 28 + 1:02d} 09:30:00",
                "open": float(o), "high": float(hi), "low": float(lo),
                "close": float(c),
                "volume": int(10000 + rng.randint(0, 3000)),
                "open_interest": int(50000 + rng.randint(0, 1000)),
            }
        )
    return bars


def test_frozen_batch1_space():
    assert OPS_NAMES[:34] == FROZEN_OPS_34
    assert FEATURE_NAMES[:27] == FROZEN_FEATURES_27
    assert FEATURE_NAMES[27] == "OI_PC"
    assert FEATURE_NAMES[34] == "DOM"
    assert FEATURE_NAMES[35] == "STRENGTH"  # 扩容批次3（2026-08-31 强弱值）
    # 2026-09-28 桌面端同步批次:36-39 本地特征 + 40-58 扩展信息源定义
    assert len(FEATURE_NAMES) == 59
    assert len(OPS_CONFIG) == 40  # 34 + 6 新算子
    assert OPS_NAMES[34] == "TS_DEMEAN_20"
    assert OPS_NAMES[35] == "BETA_20"
    assert OPS_NAMES[36] == "RESID_20"
    assert OPS_NAMES[37] == "STEP"
    assert OPS_NAMES[38] == "EMA_5"
    assert OPS_NAMES[39] == "EMA_20"


def test_step_semantics():
    x = np.array([-2.0, -0.1, 0.0, 0.1, 5.0])
    mat = np.vstack([x, x * 2])
    # STEP(token feat0)：直接调 ops 函数（execute 末尾会 zscore 破坏 0/1 语义）
    from app.services.factor_lab.ops import _step
    out = _step(x)
    assert out.tolist() == [0.0, 0.0, 0.0, 1.0, 1.0]


def test_beta_resid_math():
    rng = np.random.RandomState(7)
    y = rng.randn(200)
    x = 0.8 * y + rng.randn(200) * 0.5
    b = ts_beta(x, y, 20)
    assert 0.5 < b[-1] < 1.1  # 末窗 beta 接近真实 0.8
    r = ts_resid(x, y, 20)
    # 残差与 y 近似不相关（回归的意义）
    corr = float(np.corrcoef(r[25:], y[25:])[0, 1])
    assert abs(corr) < 0.3


def test_ema_causal_and_smooth():
    rng = np.random.RandomState(8)
    x = np.cumsum(rng.randn(500))
    from app.services.factor_lab.ops import _ema
    e20 = _ema(x, 20)
    # 因果：前缀不变性（改动未来不影响历史）
    e20_prefix = _ema(x[:300], 20)
    assert np.allclose(e20[:300], e20_prefix, atol=1e-9)
    # 平滑：EMA20 比原序列波动小
    assert e20[100:].std() < x[100:].std()


def test_demean_execute():
    bars = _make_bars(200, seed=3)
    mat = feature_matrix(bars)
    f = execute([0, FEAT_OFFSET + 34], mat)  # TS_DEMEAN_20(RET)
    assert f is not None and np.isfinite(f).all()


def test_batch2_features_present():
    bars = _make_bars(250, seed=4)
    feats = compute_features(bars)
    for name in ("OI_PC", "OI_CHG5", "OI_CHG20", "VOL_OI_MA", "SKEW20", "KURT20", "DOW", "DOM"):
        assert name in feats, name
        assert np.isfinite(feats[name]).all()
    # DOW 语义：周一=-1、周五=+1（构造 2024-01-01 周一起）
    assert feats["DOW"][0] == -1.0
    assert abs(feats["KURT20"][-1]) < 100  # 峰度有界 sanity


def test_validate_step_infected():
    step = FEAT_OFFSET + 37
    # STEP → TANH → TANH → TANH：恒正感染链
    tanh_op = FEAT_OFFSET + 12
    v = validate([0, step, tanh_op, tanh_op, tanh_op])
    assert len(v) > 0


def test_portfolio_evaluation():
    bars = _make_bars(300, seed=5)
    mat = feature_matrix(bars)
    # 两个低相关因子：RET 与 VOL_Z
    f1 = execute([0], mat)
    f2 = execute([12], mat)
    assert f1 is not None and f2 is not None
    res = evaluate_portfolio(bars, "1d", [[0], [12]], cost=0.0005)
    assert res is not None
    assert res["n_factors"] == 2
    assert 0 <= res["avg_abs_corr"] <= 1
    for k in ("ann_ret", "sortino", "calmar"):
        assert k in res["equal"]
        assert k in res["best_single"]
    # 单因子输入返回 None
    assert evaluate_portfolio(bars, "1d", [[0]], cost=0.0005) is None


def test_portfolio_diversification_beats_single():
    """低相关多因子组合：Sortino 不应显著差于最优单因子（分散化 sanity）"""
    bars = _make_bars(400, seed=6)
    res = evaluate_portfolio(bars, "1d", [[0], [12], [10]], cost=0.0005)
    assert res is not None
    assert res["equal"]["sortino"] >= res["best_single"]["sortino"] - 3.0
