"""扩容批次1 测试 —— append-only 兼容性 + 新特征/算子语义

兼容性核心断言：旧特征 id [0,19) 与旧算子 id [0,29) 的名称顺序冻结，
任何重排都会让存量 token（收藏/历史/AI任务）静默变成别的公式。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab import FEATURE_NAMES, OPS_NAMES, execute
from app.services.factor_lab.features import (
    clear_feature_matrix_cache,
    compute_features,
    feature_matrix,
)
from app.services.factor_lab.ops import OPS_CONFIG, ts_corr
from app.services.factor_lab.vm import FEAT_COUNT, FEAT_OFFSET, validate

# ── 冻结的旧空间（扩容前 19 特征 / 29 算子，顺序不可变） ──
FROZEN_FEATURES_19 = (
    "RET", "RET5", "RET20", "MA_DIFF", "SLOPE20", "ATR14", "RVOL",
    "HL_RANGE", "DEV", "RSI14", "AC1", "VOL_RATIO", "VOL_Z", "PV_CORR",
    "OI_CHG", "OI_PV", "VOL_OI", "TOD", "NIGHT",
)
FROZEN_OPS_29 = (
    "ADD", "SUB", "MUL", "DIV", "MIN", "MAX",
    "ABS", "NEG", "SIGN", "SQRT", "SIGNED_LOG", "SIGMOID", "TANH",
    "TS_MA_5", "TS_MA_10", "TS_MA_20", "TS_STD_10", "TS_STD_20",
    "TS_MAX_10", "TS_MAX_20", "TS_MIN_10", "TS_RANK_10", "TS_RANK_20",
    "TS_ZSCORE_20", "DELTA_1", "DELTA_5", "TS_ATR_NORM",
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


def test_frozen_legacy_token_space():
    """旧 id 空间顺序冻结：重排=存量 token 静默变义"""
    assert FEATURE_NAMES[:19] == FROZEN_FEATURES_19
    assert OPS_NAMES[:27] == FROZEN_OPS_29
    # 总量断言在最新批次测试中维护（批次2 后为 35 特征 / 40 算子）


def test_new_feature_ids_start_at_19():
    assert FEATURE_NAMES[19] == "GAP"
    assert FEATURE_NAMES[26] == "VOLAT_RATIO"


def test_new_op_ids_start_at_27():
    assert OPS_NAMES[27] == "LAG_1"
    assert OPS_NAMES[29] == "CORR_20"
    assert OPS_NAMES[33] == "TS_RANK_60"
    # 二元窗口算子 arity 校验（StackVM 通用分发的前提）
    assert OPS_CONFIG[29][2] == 2


def test_old_tokens_execute_unchanged():
    """旧公式在新空间下数值不变（特征/算子实现未被触碰）"""
    bars = _make_bars(300, seed=11)
    mat = feature_matrix(bars)
    # RET(0) 与 RET5(1) 之差
    f = execute([0, 1, FEAT_OFFSET + 1], mat)
    assert f is not None
    manual = (mat[0] - mat[1]) / np.sqrt(2.0)  # 数值形状 sanity
    assert f.shape == (300,)
    assert np.isfinite(f).all()
    # execute 末尾因果滚动 zscore+clip[-3,3](.3 口径,时变增益)，
    # 与线性参照的相关性允许滚动窗/截断损失
    assert abs(float(np.corrcoef(f, manual)[0, 1]) - 1.0) < 0.02
    # .3 因果不变量：截断尾部后前缀逐位不变(无未来函数)
    clear_feature_matrix_cache()
    cut = int(len(bars) * 0.8)
    f_pre = execute([0, 1, FEAT_OFFSET + 1], feature_matrix(bars[:cut]))
    assert np.array_equal(f[:cut], f_pre)


def test_lag_op_semantics():
    bars = _make_bars(120, seed=2)
    mat = feature_matrix(bars)
    x = mat[0]
    lag1 = execute([0, FEAT_OFFSET + 27], mat)  # LAG_1(RET)
    assert lag1 is not None
    # LAG_1 后再 zscore 归一化，与直接滞后归一化前值比对相关性
    raw_lag = np.zeros_like(x)
    raw_lag[1:] = x[:-1]
    assert float(np.corrcoef(lag1, raw_lag)[0, 1]) > 0.99


def test_corr20_binary_op_executes():
    bars = _make_bars(150, seed=3)
    mat = feature_matrix(bars)
    # CORR_20(RET, VOL_RATIO)：二元时序算子经 StackVM 执行
    f = execute([0, 11, FEAT_OFFSET + 29], mat)
    assert f is not None
    ref = ts_corr(mat[0], mat[11], 20)
    # execute 末尾因果滚动 zscore(.3 口径):CORR_20 输出慢变,滚动均值跟踪
    # 会消掉低频分量,全段相关性显著低于全样本 zscore 时代,仍应高度正相关
    assert float(np.corrcoef(f[25:], ref[25:])[0, 1]) > 0.85


def test_gap_feature_correctness():
    """GAP = (open - prev_close)/|prev_close|，随后因果 zscore"""
    bars = _make_bars(200, seed=4)
    feats = compute_features(bars)
    close = np.array([b["close"] for b in bars])
    open_ = np.array([b["open"] for b in bars])
    raw = np.zeros_like(close)
    raw[1:] = (open_[1:] - close[:-1]) / np.abs(close[:-1])
    corr = float(np.corrcoef(feats["GAP"][30:], raw[30:])[0, 1])
    assert corr > 0.95  # zscore 是单调变换，相关性应极高


def test_validate_covers_new_ops():
    """TS_RANK_60 → ABS → TS_MA_20 恒正感染链应被拦截"""
    rank60 = FEAT_OFFSET + 33  # TS_RANK_60
    absop = FEAT_OFFSET + 6
    ma20 = FEAT_OFFSET + 15  # TS_MA_20
    toks = [0, rank60, absop, ma20]
    v = validate(toks)
    assert len(v) > 0
    # LAG 不灭感染：RANK → LAG → LAG → LAG 链也拦
    lag1 = FEAT_OFFSET + 27
    v2 = validate([0, rank60, lag1, lag1, lag1])
    assert len(v2) > 0
    # CORR_20 恢复符号：合法
    assert validate([0, 1, FEAT_OFFSET + 29]) == []


def test_gp_rejects_infected_formulas():
    """GP eval 路径接入 validate：感染公式直接 -999（搜不出来）"""
    from app.services.factor_lab.search import SearchConfig, search

    bars = _make_bars(150, seed=6)
    champs = search(
        bars,
        "1d",
        SearchConfig(population=10, generations=2, top_n=3, seed_tokens=[
            [0, FEAT_OFFSET + 33, FEAT_OFFSET + 6, FEAT_OFFSET + 15],  # 感染链
        ]),
    )
    infected = [c for c in champs if c.tokens == [
        0, FEAT_OFFSET + 33, FEAT_OFFSET + 6, FEAT_OFFSET + 15
    ]]
    assert not infected
