"""P3 多周期 IC 测试 —— fwd_ret / ts_ic_h / composite 三周期均值"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.evaluate import (
    _fwd_ret,
    _ts_ic,
    _ts_ic_h,
    evaluate_factor,
    next_ret,
)


def test_fwd_ret_formula():
    close = np.array([100.0, 110.0, 121.0, 133.1, 120.0, 130.0])
    fwd2 = _fwd_ret(close, 2)
    assert abs(fwd2[0] - (121.0 - 100.0) / 100.0) < 1e-12
    assert abs(fwd2[1] - (133.1 - 110.0) / 110.0) < 1e-12
    assert abs(fwd2[2] - (120.0 - 121.0) / 121.0) < 1e-12
    assert abs(fwd2[3] - (130.0 - 133.1) / 133.1) < 1e-12
    # 尾部 h 根无未来 → 0
    assert fwd2[4] == 0.0 and fwd2[5] == 0.0
    assert list(_fwd_ret(close, 0)) == [0.0] * len(close)


def test_ts_ic_h_perfect_predictor():
    """因子=未来 5 根收益（构造性前瞻）→ ic5 ≈ 1；ic1 不必然为 1"""
    rng = np.random.RandomState(3)
    n = 400
    steps = rng.randn(n) * 0.01
    close = 3000.0 * np.cumprod(1 + steps)
    fwd5 = _fwd_ret(close, 5)
    factor = fwd5.copy()  # 完美前瞻因子
    ic5 = _ts_ic_h(factor, close, 5)
    assert ic5 > 0.99


def test_ts_ic_h_negative_when_factor_inverts():
    """因子与 20 根前视收益完全反相关 → ic20 ≈ -1"""
    rng = np.random.RandomState(4)
    n = 400
    close = 3000.0 * np.cumprod(1 + rng.randn(n) * 0.01)
    fwd20 = _fwd_ret(close, 20)
    ic20 = _ts_ic_h(-fwd20, close, 20)
    assert ic20 < -0.99


def test_evaluate_factor_reports_multi_ic_composite_keeps_h1():
    """报告三周期 IC；composite 的 IC 项保持 h=1（E3 回滚后口径）"""
    rng = np.random.RandomState(5)
    n = 600
    close = 3000.0 * np.cumprod(1 + rng.randn(n) * 0.01)
    factor = np.tanh(rng.randn(n)) * 2
    m = evaluate_factor(factor, close, cost=0.0003, periods=243)
    for k in ("ts_ic", "ts_ic_5", "ts_ic_20"):
        assert k in m
    # h=1 口径与旧 _ts_ic 一致（向后兼容）
    assert abs(m["ts_ic"] - _ts_ic(factor, next_ret(close))) < 1e-12
    # composite 只用 ts_ic（h=1），不含 5/20 周期项（重建公式核对）
    expected = (
        0.30 * max(-1.0, min(1.0, m["ann_ret"]))
        + 0.15 * m["sortino"]
        + 0.10 * m["calmar"]
        + 0.20 * m["ts_ic"]
        + 0.05 * m["symmetry"]
        + 0.05 * m["turnover_q"]
        + 0.10 * m["consistency"]
    ) * m["oos_mult"]
    assert abs(m["composite"] - expected) < 1e-9
