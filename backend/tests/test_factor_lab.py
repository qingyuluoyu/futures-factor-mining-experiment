"""factor_lab 内核单元测试 —— 特征/算子/VM/评估/搜索"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab import (
    FEATURE_NAMES,
    execute,
    feature_matrix,
    is_constant,
    op_arity,
    to_text,
    validate,
)
from app.services.factor_lab.scoring.evaluate import (
    evaluate_factor,
    position_from_factor,
)
from app.services.factor_lab.search import SearchConfig, search
from app.services.factor_lab.vm import FEAT_COUNT, FEAT_OFFSET


def _make_bars(n: int = 120, seed: int = 0) -> list[dict]:
    """合成 OHLCV bars（带趋势 + 噪声）"""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    bars: list[dict] = []
    for i, c in enumerate(close):
        hi = c + abs(rng.randn()) * 0.5
        lo = c - abs(rng.randn()) * 0.5
        bars.append(
            {
                "time": f"2024-01-{i + 1:02d}",
                "open": float(c),
                "high": float(hi),
                "low": float(lo),
                "close": float(c),
                "volume": int(1000 + rng.randint(-200, 200)),
            }
        )
    return bars


def test_feature_matrix_shape_and_finite():
    bars = _make_bars(120)
    mat = feature_matrix(bars)
    # 无扩展信息源时矩阵只出基础行(桌面端同步口径,含 x_* 才出全集)
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    assert mat.shape[0] == BASE_FEAT_COUNT
    assert mat.shape[1] == 120
    assert np.all(np.isfinite(mat))


def test_vm_execute_single_feature():
    bars = _make_bars(120)
    mat = feature_matrix(bars)
    tokens = [0]  # 仅 RET 特征
    factor = execute(tokens, mat)
    assert factor is not None
    assert factor.shape == (120,)
    assert np.all(np.isfinite(factor))


def test_vm_execute_compound_formula():
    """SUB(RET, 5周期均值(DEV))：二元+一元"""
    bars = _make_bars(120)
    mat = feature_matrix(bars)
    ret_idx = 0
    dev_idx = FEATURE_NAMES.index("DEV")
    sub_op = FEAT_OFFSET + 1  # SUB
    ma_op = FEAT_OFFSET + 13  # TS_MA_5
    tokens = [ret_idx, dev_idx, ma_op, sub_op]  # 后缀：RET DEV TS_MA_5 SUB
    factor = execute(tokens, mat)
    assert factor is not None
    assert not is_constant(factor)


def test_validate_detects_positive_infection():
    """TS_RANK_20 → ABS → TS_MA_20：末尾恒正感染应被检出"""
    from app.services.factor_lab.ops import OPS_NAMES

    rank_t = FEAT_OFFSET + OPS_NAMES.index("TS_RANK_20")
    abs_t = FEAT_OFFSET + OPS_NAMES.index("ABS")
    ma_t = FEAT_OFFSET + OPS_NAMES.index("TS_MA_20")
    # feat(0) TS_RANK_20 → ABS → TS_MA_20
    tokens = [0, rank_t, abs_t, ma_t]
    violations = validate(tokens)
    assert len(violations) > 0


def test_validate_clean_formula():
    """单特征公式无违规"""
    assert validate([0]) == []
    assert validate([0, 1, FEAT_OFFSET + 0]) == []  # RET RET ADD


def test_evaluate_factor_returns_metrics():
    bars = _make_bars(150)
    mat = feature_matrix(bars)
    close = np.array([b["close"] for b in bars])
    factor = execute([FEATURE_NAMES.index("DEV")], mat)
    metrics = evaluate_factor(factor, close, cost=0.0003, periods=243)
    for key in (
        "ann_ret",
        "sortino",
        "calmar",
        "ts_ic",
        "composite",
        "oos_sortino",
    ):
        assert key in metrics
        assert np.isfinite(metrics[key])


def test_position_neutral_band():
    factor = np.array([0.01, 0.5, -0.5, 0.0])
    pos = position_from_factor(factor, neutral=0.05)
    assert pos[0] == 0.0  # |tanh(0.01)|<0.05 → 空仓
    assert pos[1] > 0 and pos[2] < 0


def test_text_readable():
    from app.services.factor_lab.ops import OPS_NAMES

    add_t = FEAT_OFFSET + OPS_NAMES.index("ADD")
    txt = to_text([0, 1, add_t])
    assert "+" in txt or "加" in txt or "RET" in txt


def test_search_returns_champions():
    bars = _make_bars(150, seed=1)
    cfg = SearchConfig(population=12, generations=4, max_depth=3, top_n=3, seed=7)
    champs = search(bars, "1d", cfg)
    assert len(champs) >= 1
    assert all(c.tokens for c in champs)
    assert all(isinstance(c.text, str) for c in champs)
    assert all("composite" in c.metrics for c in champs)


def test_factor_strategy_signal():
    """因子策略：合法公式产出合法动作"""
    from app.services.ai_trading.strategies.factor import (
        compute_factor_signal,
        normalize_factor_params,
    )

    bars = _make_bars(150, seed=2)
    p = normalize_factor_params({"factor_tokens": [8]})  # DEV 特征
    sig = compute_factor_signal(bars, p, "both", None)
    assert sig["action"] in ("hold", "open_long", "open_short", "close")
    assert sig["parse_ok"] is True


def test_factor_strategy_empty_tokens():
    """空公式 → hold"""
    from app.services.ai_trading.strategies.factor import compute_factor_signal

    bars = _make_bars(100)
    sig = compute_factor_signal(bars, {"factor_tokens": []}, "both", None)
    assert sig["action"] == "hold"
    assert sig["signal"] == "no_factor"


def test_factor_snapshot_for_llm():
    """P4：因子快照供 LLM 注入"""
    from app.services.ai_trading.strategies.factor import factor_snapshot

    bars = _make_bars(150, seed=4)
    snap = factor_snapshot([8], bars)
    assert snap is None or isinstance(snap["position"], float)


def test_ab_compare():
    """P5：因子 vs 买入持有对比"""
    from app.services.factor_lab.scoring.ab_compare import compare

    bars = _make_bars(150, seed=3)
    r = compare(bars, "1d", [[0], [8]], 0.0003)
    assert r["bars"] == 150
    assert "baseline" in r and "sortino" in r["baseline"]
    assert len(r["factors"]) == 2
    assert all("delta_sortino" in f for f in r["factors"] if f.get("ok"))


# ── 防过拟合改造：walk-forward / train-test split / OOS 淘汰 ──────────────


def test_split_bars_ratio():
    """split_bars 按比例切分，两段长度合理"""
    from app.services.factor_lab.scoring.walk_forward import split_bars

    bars = _make_bars(600, seed=1)
    train, test = split_bars(bars, 0.7)
    assert len(train) == 420
    assert len(test) == 180
    # 训练段无重叠
    assert train[-1]["time"] != test[0]["time"]


def test_split_bars_disabled():
    """train_ratio=0 时不切分，返回完整 bars（向后兼容）"""
    from app.services.factor_lab.scoring.walk_forward import split_bars

    bars = _make_bars(100, seed=1)
    train, test = split_bars(bars, 0.0)
    assert len(train) == 100
    assert len(test) == 0


def test_evaluate_on_segment_returns_pure_metrics():
    """在任意子段执行因子，返回纯指标（不含 composite）"""
    from app.services.factor_lab.scoring.walk_forward import evaluate_on_segment

    bars = _make_bars(150, seed=2)
    m = evaluate_on_segment([8], bars, "1d", 0.0003)  # DEV 特征
    assert m is not None
    for key in ("ann_ret", "sortino", "calmar", "ts_ic"):
        assert key in m and np.isfinite(m[key])
    assert "composite" not in m  # 纯指标，无 composite


def test_walk_forward_eval_structure():
    """walk-forward 返回正确的折数与稳健性标志"""
    from app.services.factor_lab.scoring.walk_forward import walk_forward_eval

    bars = _make_bars(700, seed=3)  # P1-7 后每段须 ≥ MIN_TEST_BARS=120
    wf = walk_forward_eval([8], bars, "1d", 0.0003, n_folds=3)
    assert wf is not None
    assert wf["n_folds"] == 3
    assert len(wf["folds"]) == 3
    assert all("train" in f and "test" in f for f in wf["folds"])
    assert isinstance(wf["wf_stable"], bool)
    assert "wf_mean_test_sortino" in wf


def test_walk_forward_too_short_returns_none():
    """bars 不足时 walk-forward 返回 None（避免无统计意义的折）"""
    from app.services.factor_lab.scoring.walk_forward import walk_forward_eval

    bars = _make_bars(100, seed=1)
    wf = walk_forward_eval([8], bars, "1d", 0.0003, n_folds=6)
    assert wf is None  # 100/7 < MIN_TEST_BARS


def test_search_with_train_test_split():
    """开启 train_ratio 后，champion 带 train/test 两套指标"""
    bars = _make_bars(300, seed=5)
    cfg = SearchConfig(
        population=12,
        generations=4,
        max_depth=3,
        top_n=5,
        seed=7,
        train_ratio=0.7,
    )
    champs = search(bars, "1d", cfg)
    # 搜出来的冠军：严格筛通过的 test_metrics.sortino>0；
    # 若全军覆没走兜底回退，则带 overfit_warning 且 test_metrics.sortino 可能≤0。
    for c in champs:
        if "test_metrics" in c.metrics and "overfit_warning" not in c.metrics:
            # 严格通过测试验证的因子，train/test 都应存在且 sortino 为正
            assert "train_metrics" in c.metrics
            assert c.metrics["test_metrics"]["sortino"] > 0.0
            assert np.isfinite(c.metrics["train_metrics"]["sortino"])


def test_search_with_test_recent_bars():
    """test_recent_bars 强制尾部作测试段（方案 E）"""
    bars = _make_bars(300, seed=8)
    cfg = SearchConfig(
        population=12,
        generations=4,
        max_depth=3,
        top_n=3,
        seed=7,
        test_recent_bars=60,
    )
    champs = search(bars, "1d", cfg)
    # 不应抛异常；严格通过的 test_metrics.sortino>0，兜底回退的带 overfit_warning
    for c in champs:
        if "test_metrics" in c.metrics and "overfit_warning" not in c.metrics:
            assert c.metrics["test_metrics"]["sortino"] > 0.0


def test_search_default_anti_overfit_enabled():
    """schema 默认值已开启防过拟合（train_ratio=0.7, walk_forward_folds=3）

    验证 API 层 SearchRequest 不传防过拟合参数时，默认带入推荐配置，
    而非回到改造前的关闭行为。这是面向终端用户的默认安全网。
    """
    from app.schemas.factor_lab import SearchRequest

    req = SearchRequest(symbol="rb888", timeframe="1d")
    assert req.train_ratio == 0.7
    assert req.walk_forward_folds == 3
    assert req.test_recent_bars == 0  # 此项保持 0（跨周期语义不一，留手动）


def test_search_backward_compat_no_split():
    """三个防过拟合参数【显式置 0】时，行为与改造前一致（无 train/test 子键）

    注：schema 默认已开启 0.7+3，此处显式传 0 模拟"关闭防过拟合"的回退路径，
    供需要复现改造前行为的场景（如对照实验）使用。
    """
    bars = _make_bars(150, seed=1)
    cfg = SearchConfig(
        population=12,
        generations=4,
        max_depth=3,
        top_n=3,
        seed=7,
        train_ratio=0.0,
        test_recent_bars=0,
        walk_forward_folds=0,
    )
    champs = search(bars, "1d", cfg)
    assert len(champs) >= 1
    for c in champs:
        # 未开启切分时不应附带 train/test 子键（向后兼容）
        assert "train_metrics" not in c.metrics
        assert "test_metrics" not in c.metrics
        assert "walk_forward" not in c.metrics


def test_champion_payload_recursive_round():
    """champion_payload 对嵌套 dict/list 不崩溃（walk_forward 折明细）"""
    from app.api.factor_lab.common import champion_payload

    # 构造含嵌套结构的 champion dict，模拟 walk-forward 开启后的 metrics
    fake = [
        {
            "tokens": [0, 8],
            "text": "RET+DEV",
            "composite": 1.23456,
            "metrics": {
                "ann_ret": 0.123456,
                "sortino": 1.5,
                "train_metrics": {"ann_ret": 0.2, "sortino": 1.8, "ts_ic": 0.05},
                "test_metrics": {"ann_ret": 0.1, "sortino": 1.2, "ts_ic": 0.03},
                "walk_forward": {
                    "folds": [
                        {"train": {"sortino": 1.1}, "test": {"sortino": 0.9}},
                    ],
                    "wf_stable": True,
                    "n_folds": 1,
                },
                "oos_negative": False,
            },
        }
    ]
    out = champion_payload(fake)  # 原实现会 float({...}) 崩溃
    assert len(out) == 1
    m = out[0]["metrics"]
    # 嵌套结构保留且数值被 round
    assert m["train_metrics"]["sortino"] == 1.8
    assert m["walk_forward"]["wf_stable"] is True  # bool 类型保留
    assert m["walk_forward"]["folds"][0]["test"]["sortino"] == 0.9
    assert m["ann_ret"] == 0.1235  # 标量被 round 到 4 位
