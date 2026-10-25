"""P0 口径对齐测试 —— 实盘离散仓位评估 + 试验登记精简"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.evaluate import (
    LIVE_ENTRY,
    evaluate_factor,
    evaluate_factor_live,
    position_from_factor,
    position_live_discrete,
)
from app.services.factor_lab.persist.trial_svc import _slim_champions


def test_live_discrete_hysteresis():
    """迟滞状态机：0.3 入场、0.05 平仓、中间带保持"""
    factor = np.array([0.10, 0.35, 0.20, 0.04, -0.10, -0.40, -0.20, 0.50])
    pos = position_live_discrete(factor)
    # 0.10: 中间带，无仓 → 0
    # 0.35: > 0.3 → +1；0.20: 中间带持有 → +1；0.04: < 0.05 → 平仓
    # -0.10: 中间带无仓 → 0；-0.40: < -0.3 → -1；-0.20: 持有 → -1；0.50: > 0.3 → +1
    assert list(pos) == [0.0, 1.0, 1.0, 0.0, 0.0, -1.0, -1.0, 1.0]


def test_live_discrete_matches_entry_threshold():
    """阈值边界：恰好 0.3 不入场（实盘 factor.py 用 > 严格比较）"""
    factor = np.array([LIVE_ENTRY, -LIVE_ENTRY, 0.0])
    assert list(position_live_discrete(factor)) == [0.0, 0.0, 0.0]


def test_evaluate_factor_live_shape():
    """返回实盘口径指标 dict，字段齐全"""
    rng = np.random.RandomState(7)
    factor = rng.randn(300)
    close = 3000.0 * np.cumprod(1 + rng.randn(300) * 0.01)
    m = evaluate_factor_live(factor, close, cost=0.0005, periods=243)
    for k in ("ann_ret", "sortino", "calmar", "avg_turnover",
              "exposure", "n_trades"):
        assert k in m
    assert 0.0 <= m["exposure"] <= 1.0
    assert isinstance(m["n_trades"], int)


def test_live_vs_continuous_positions_differ():
    """离散仓位与连续仓位不同（正是口径裂缝要暴露的事实）"""
    factor = np.sin(np.linspace(0, 20, 200))
    cont = position_from_factor(factor)
    disc = position_live_discrete(factor)
    assert not np.allclose(cont, disc)


def test_slim_champions_flattens_metrics():
    champions = [
        {
            "tokens": [1, 2, 3],
            "composite": 1.5,
            "metrics": {
                "ann_ret": 0.4,
                "train_metrics": {"ann_ret": 0.6},
                "test_metrics": {"ann_ret": 0.1},
                "walk_forward": {"wf_stable": True},
            },
        }
    ]
    slim = _slim_champions(champions)
    assert slim[0]["train_ann"] == 0.6
    assert slim[0]["test_ann"] == 0.1
    assert slim[0]["wf_stable"] is True
    assert slim[0]["overfit_warning"] is False


def test_composite_ann_term_clamped():
    """年化项截断 ±1.0：极端年化不再无限抬高 composite（量纲失衡修复）"""
    rng = np.random.RandomState(3)
    close = 3000.0 * np.cumprod(1 + rng.randn(400) * 0.02)
    # 完美预测因子（度量函数单测允许用未来信息）：factor=当期收益符号，
    # pos*ret = |ret| → 年化 = mean|ret|×243 轻松爆表
    ret = np.zeros_like(close)
    ret[:-1] = (close[1:] - close[:-1]) / close[:-1]
    factor = np.sign(ret)
    m = evaluate_factor(factor, close, cost=0.0, periods=243)
    assert m["ann_ret"] > 1.0  # 原始年化确实爆表
    # 截断后 composite 上界 = (各项满分 + ann 项封顶 0.30) × oos_mult(≤1.2)
    upper = (0.30 + 0.15 * 20 + 0.10 * 10 + 0.20 + 0.05 + 0.05 + 0.10) * 1.2
    assert m["composite"] <= upper + 1e-9
    # 未截断的话 ann 项会是 0.30*3.03≈0.91 > 0.30，composite 必然超上界
    unclamped = m["composite"] + 0.30 * (m["ann_ret"] - 1.0)
    assert unclamped > upper
