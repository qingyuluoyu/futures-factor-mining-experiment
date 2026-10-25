"""P1 实盘口径校验测试 —— next_ret_open / live_fill 指标 / 严格筛门

口径背景：因子评估与回测引擎同为"当根收盘成交"，实盘市价单在次根开盘
附近成交。live_fill 指标用 open[t+1]→open[t+2] 收益度量这一缺口，
防高换手因子收盘口径为正、实盘口径为负。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab import feature_matrix
from app.services.factor_lab.scoring.evaluate import (
    evaluate_factor,
    live_fill_metrics,
    next_ret,
    next_ret_open,
)
from app.services.factor_lab.scoring.walk_forward import evaluate_on_segment
from app.services.factor_lab.search import _dedup_top


def _make_bars(n: int, seed: int, gap: float = 0.0) -> list[dict]:
    """合成日 bar；gap>0 时 open[t+1] 系统性高于 close[t]（缺口市场）"""
    rng = np.random.RandomState(seed)
    close = 3000.0 * np.cumprod(1 + rng.randn(n) * 0.01)
    opens = np.empty_like(close)
    opens[0] = close[0]
    for i in range(1, n):
        opens[i] = close[i - 1] * (1.0 + gap)
    return [
        {
            "time": f"2024-{i % 12 + 1:02d}-{i % 28 + 1:02d}",
            "open": float(opens[i]),
            "high": float(max(opens[i], close[i]) * 1.005),
            "low": float(min(opens[i], close[i]) * 0.995),
            "close": float(close[i]),
            "volume": int(1000 + rng.randint(0, 500)),
            "open_interest": int(50000 + rng.randint(0, 1000)),
        }
        for i in range(n)
    ]


def test_next_ret_open_formula():
    """ret[t] = (open[t+2] - open[t+1]) / open[t+1]，最后两根为 0"""
    opens = np.array([100.0, 110.0, 121.0, 100.0])
    ret = next_ret_open(opens)
    assert abs(ret[0] - (121.0 - 110.0) / 110.0) < 1e-12
    assert abs(ret[1] - (100.0 - 121.0) / 121.0) < 1e-12
    assert ret[2] == 0.0 and ret[3] == 0.0
    # 退化保护：短序列
    assert list(next_ret_open(np.array([1.0, 2.0]))) == [0.0, 0.0]


def test_no_gap_equivalence():
    """无缺口市场（open[t+1]=close[t]）：开盘口径与收盘口径每根收益一致"""
    rng = np.random.RandomState(7)
    close = 3000.0 * np.cumprod(1 + rng.randn(300) * 0.01)
    opens = np.concatenate([[close[0]], close[:-1]])
    rc = next_ret(close)
    ro = next_ret_open(opens)
    # 开盘口径覆盖 t=0..T-3，与收盘口径前 T-2 项一致
    assert np.allclose(ro[:-2], rc[:-2], atol=1e-12)


def test_live_fill_metrics_fields():
    bars = _make_bars(400, seed=11, gap=0.004)
    mat = feature_matrix(bars)
    opens = np.array([b["open"] for b in bars], dtype=float)
    close = np.array([b["close"] for b in bars], dtype=float)
    factor = mat[0]  # RET 特征
    lf = live_fill_metrics(factor, opens, cost=0.0003, periods=243)
    assert set(lf) == {"live_fill_ann_ret", "live_fill_sortino"}
    assert np.isfinite(lf["live_fill_ann_ret"])
    assert np.isfinite(lf["live_fill_sortino"])
    # 与收盘口径同源对照（同一因子）
    cm = evaluate_factor(factor, close, cost=0.0003, periods=243)
    assert cm["ann_ret"] != lf["live_fill_ann_ret"]  # 缺口市场两者应不同


def test_evaluate_on_segment_reports_live_fill():
    bars = _make_bars(300, seed=13)
    m = evaluate_on_segment([0], bars, "1d", 0.0003)
    assert m is not None
    assert "live_fill_sortino" in m and "live_fill_ann_ret" in m
    # 无缺口合成 bar（open==close）：两口径应接近
    assert abs(m["sortino"] - m["live_fill_sortino"]) < 1.0


def test_live_fill_gate_in_dedup_top(monkeypatch):
    """门开：实盘口径为负的候选不晋级（走兜底回退）；门关：不受影响"""
    from app.services.factor_lab import search as search_mod
    from app.services.factor_lab.scoring.evaluate import evaluate_factor as ef

    bars = _make_bars(600, seed=17)
    train, test = bars[:420], bars[420:]
    mat = feature_matrix(train)
    close = np.array([b["close"] for b in train], dtype=float)
    champs = []
    for feat in (0, 1, 2):
        m = ef(mat[feat], close, cost=0.0005, periods=243)
        champs.append((float(m["composite"]), [feat], m))

    real_slice = search_mod.evaluate_on_slice

    def fake_slice(tokens, all_bars_, lo, hi, timeframe, cost):
        m = dict(real_slice(tokens, all_bars_, lo, hi, timeframe, cost))
        m["sortino"] = 1.0  # 收盘口径强制为正（聚焦验证门逻辑本身）
        m["live_fill_sortino"] = -1.0  # 实盘口径强制为负
        return m

    monkeypatch.setattr(search_mod, "evaluate_on_slice", fake_slice)

    common = dict(
        top_n=5,
        test_bars=test,
        timeframe="1d",
        cost=0.0005,
        use_test=True,
        walk_forward_folds=0,
        train_bars=train,
        trials=1200,
    )
    out_off = _dedup_top(list(champs), live_fill_gate=False, **common)
    assert out_off
    assert not any((c.metrics or {}).get("overfit_warning") for c in out_off)

    out_on = _dedup_top(list(champs), live_fill_gate=True, **common)
    # 严格筛全灭 → 兜底回退，全部带 overfit_warning
    assert out_on
    assert all((c.metrics or {}).get("overfit_warning") for c in out_on)
