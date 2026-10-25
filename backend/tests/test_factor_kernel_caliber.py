"""批次二内核口径修复 —— 不变量测试（桌面端 verify-kernel-tests.py 的服务端移植）

覆盖：P0-3 ts_ic 对齐 / P2-19 calmar 零基线 / P1-6 资金曲线右移对账 /
P1-8 walk-forward 训练段标记 / P2-10 反手计数 / P2-12 文案守卫 / 版本戳。

P1-4（OOS 为负改加性罚分）**未采纳**：服务端 E-Z1 三臂 A/B（2026-08-30，
docs/experiments/factor-improvements-RESULTS.md Z1 节）实测加性臂测试段
sortino -46%、严格筛冠军 -55%，预登记判 FAIL 回滚——零平台是特性而非
缺陷，test_oos_zero_platform 守护该结论（用户 2026-09-01 拍板）。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.evaluate import (
    _calmar,
    _ts_ic,
    evaluate_factor,
    evaluate_factor_live,
    next_ret,
    position_from_factor,
    position_live_discrete,
)


def _make_close(n: int = 300, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.02, n)
    return 1000.0 * np.cumprod(1.0 + steps)


# ============ P0-3：ts_ic 与收益对齐 ============


def test_ts_ic_alignment():
    """因子恰为下根收益本身时，IC 应 ≈ 1（修复前错位一根 ≈ 0）"""
    close = _make_close()
    ret = next_ret(close)
    ic = _ts_ic(ret.copy(), ret)  # factor = ret（已是 t→t+1 前向收益）
    assert ic > 0.999, f"IC 应≈1.0，实际 {ic:.4f}"
    # 反向因子 → IC ≈ -1
    ic_neg = _ts_ic(-ret, ret)
    assert ic_neg < -0.999, f"反向因子 IC 应≈-1.0，实际 {ic_neg:.4f}"


# ============ P2-19：calmar 回撤零基线 ============


def test_calmar_zero_baseline():
    """开盘即跌的回撤从 0（期初）起算，不再低估"""
    periods = 243
    pnl = np.full(100, -0.001)
    cal = _calmar(pnl, periods)
    # 新口径 dd=0.1（0→-0.1），ann=-0.243 → calmar=-2.43；旧口径 dd=0.099 → -2.454…
    assert abs(cal - (-0.243 / 0.1)) < 1e-9, f"零基线回撤口径不符: {cal}"


# ============ P1-4 未采纳：零平台守护（E-Z1） ============


def test_oos_zero_platform():
    """样本外为负 → composite 恰为 0.0（零平台），不得改成负分

    桌面端批次二的 P1-4（base-1+min(0,oos_sor)*0.1 加性罚分）与 E-Z1
    实验结论冲突（加性臂冠军产出 -55%），服务端保持 ×0.0。若未来要重启
    该方向，先重跑 E-Z1 并按预登记规则判定，不要按注释"修 bug"。
    """
    n = 400
    # 前 300 根涨、后 100 根跌；恒做多 → 训练好看、样本外（后 25%）亏损
    close_a = 1000.0 * np.cumprod(
        np.concatenate([np.full(300, 1.005), np.full(100, 0.995)])
    )
    m_a = evaluate_factor(np.full(n, 3.0), close_a, cost=0.0, periods=243)
    assert m_a["oos_negative"], "A 应为样本外为负"
    assert m_a["oos_mult"] == 0.0
    assert m_a["composite"] == 0.0, (
        f"零平台口径：composite 应恰为 0.0，实际 {m_a['composite']}"
    )


# ============ P1-6：回测资金曲线右移一根 ============


def test_equity_curve_right_shift():
    """第 t 根显示 t-1 决策已实现盈亏：首根 equity=initial_cash；
    曲线总收益与指标累计 pnl 对账（末根平盘 + 零成本时严格相等）。

    计算式与 api_backtest_factor 同式镜像（该处内联在 handler 中），
    内核函数（evaluate_factor/position_from_factor/next_ret）为同源。
    """
    from app.services.factor_lab import execute, feature_matrix

    rng = np.random.default_rng(5)
    rets = rng.normal(0, 0.015, 299).tolist() + [0.0]  # 末根平盘
    close = 1000.0 * np.cumprod(1.0 + np.array(rets))
    n = len(close)
    periods = 243
    bars = [
        {
            "time": f"2026-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00",
            "open": float(close[i]),
            "high": float(close[i] * 1.001),
            "low": float(close[i] * 0.999),
            "close": float(close[i]),
            "volume": 1000,
            "open_interest": 5000,
        }
        for i in range(n)
    ]
    factor = execute([0], feature_matrix(bars))  # RET 特征
    assert factor is not None
    metrics = evaluate_factor(factor, close, cost=0.0, periods=periods)
    pos = position_from_factor(factor)
    ret = next_ret(close)
    # —— 与 api_backtest_factor 相同的 P1-6 口径 ——
    prev = np.roll(pos, 1)
    prev[0] = 0.0
    pnl = pos * ret - np.abs(pos - prev) * 0.0
    realized = np.roll(pnl, 1)
    realized[0] = 0.0
    initial = 100000.0
    equity = initial * (1.0 + np.cumsum(realized))

    assert equity[0] == initial, "首根不应有已实现盈亏（P1-6 右移）"
    total_realized = equity[-1] / initial - 1.0
    total_from_metrics = metrics["ann_ret"] * n / periods
    # 末根 pnl=0 → 两侧严格相等（浮点累加差即可）
    assert abs(total_realized - total_from_metrics) < 1e-9, (
        f"曲线与指标对账不符: {total_realized} vs {total_from_metrics}"
    )


# ============ P1-8：walk-forward 训练段标记 ============


def _wf_bars() -> list[dict]:
    """480 根日线：[0:360) 交替涨跌 | [360:480) 严格递增全正收益

    三折切分（P1-7 后 seg=120，每段须 ≥ MIN_TEST_BARS）：折 1/2 的测试段
    落在交替区（特征经因果 zscore 后 z[t] 与 r[t+1] 恒反号 → 必亏），
    折 3 测试段为严格递增区（z 恒正、下根收益恒正 → 逐根盈利无回撤）——
    用于验证 wf_stable 只看样本外折。
    注：上行区不能用常数收益——因果 zscore 会把常数压成 0（零仓位）。
    """
    rets = [0.20 if i % 2 == 0 else -0.20 for i in range(360)]
    rets += [0.02 + 0.001 * i for i in range(120)]
    close = 1000.0 * np.cumprod(1.0 + np.array(rets))
    return [
        {
            "time": f"2024-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}T00:00:00",
            "open": float(close[i]),
            "high": float(close[i] * 1.001),
            "low": float(close[i] * 0.999),
            "close": float(close[i]),
            "volume": 1000,
            "open_interest": 5000,
        }
        for i in range(len(close))
    ]


def test_walk_forward_train_len():
    """train_len 覆盖的折标记 in_train，wf_stable 只按样本外折判定"""
    from app.services.factor_lab.scoring.walk_forward import walk_forward_eval

    bars = _wf_bars()
    tokens = [0]  # RET 特征（动能：当根收益方向持仓）

    # 训练段长度 360：折 1/2（test_end ≤ 360）在训练段内，折 3 为样本外
    wf = walk_forward_eval(tokens, bars, "1d", 0.0, 3, train_len=360)
    assert wf is not None
    assert [f["in_train"] for f in wf["folds"]] == [True, True, False]
    assert wf["n_oos_folds"] == 1
    # 样本外只有折 3（上行区）→ 稳定；训练段内的两折亏损不参与判定
    assert wf["wf_stable"] is True
    assert abs(wf["wf_mean_test_sortino"] - wf["folds"][2]["test"]["sortino"]) < 1e-12

    # train_len=None（报告路径旧行为）：全部折参与判定 → 交替区两折拖垮
    wf_all = walk_forward_eval(tokens, bars, "1d", 0.0, 3)
    assert wf_all is not None
    assert wf_all["n_oos_folds"] == 3
    assert all(f["in_train"] is False for f in wf_all["folds"])
    assert wf_all["wf_stable"] is False


# ============ P2-10：实盘交易次数补计直接反手 ============


def test_live_n_trades_counts_reversal():
    """状态变化即一次交易：+1→-1 直接反手此前漏计"""
    factor = np.array([0.0, 0.5, -0.5, 0.5, 0.02])
    # 离散仓位：[0, +1, -1, +1, 0]——开仓、反手、反手、平仓
    assert list(position_live_discrete(factor)) == [0.0, 1.0, -1.0, 1.0, 0.0]
    close = np.full(len(factor), 100.0)
    m = evaluate_factor_live(factor, close, cost=0.0, periods=243)
    # 旧口径只计从空仓开出的 → 1；新口径含两次直接反手 → 3
    assert m["n_trades"] == 3


# ============ P2-12：中文文案守卫 ============


def test_express_text_coverage():
    """文案表必须覆盖全部注册算子/特征，防扩容再漏配（公式露出英文名）"""
    from app.services.factor_lab.express import _FEAT_TEXT, _OP_TEXT
    from app.services.factor_lab.features import FEATURE_NAMES
    from app.services.factor_lab.ops import OPS_NAMES

    missing_ops = sorted(set(OPS_NAMES) - set(_OP_TEXT))
    missing_feats = sorted(set(FEATURE_NAMES) - set(_FEAT_TEXT))
    assert not missing_ops, f"算子缺中文文案: {missing_ops}"
    assert not missing_feats, f"特征缺中文文案: {missing_feats}"


# ============ 版本戳（交接清单第 2 步） ============


def test_kernel_version_matches_handoff():
    from app.services.factor_lab import kernel_version

    assert kernel_version() == "pykernel-factor-2026-09-01.3"


def test_champion_payload_kernel_stamp():
    """产出路径打当前口径戳；透传路径不覆盖既有戳、不给旧记录补戳"""
    from app.api.factor_lab.common import champion_payload
    from app.services.factor_lab import kernel_version

    champs = [
        {"tokens": [0], "text": "x", "composite": 1.0, "metrics": {"sortino": 1.2}}
    ]
    stamped = champion_payload(champs, stamp_kernel=True)
    assert stamped[0]["metrics"]["kernel_version"] == kernel_version()
    # 原 dict 不被污染
    assert "kernel_version" not in champs[0]["metrics"]

    # 透传：桌面端/已存任务自带的戳原样保留
    passthrough = champion_payload(
        [
            {
                "tokens": [0],
                "text": "x",
                "composite": 1.0,
                "metrics": {"kernel_version": "pykernel-factor-2026-08-23.1"},
            }
        ]
    )
    assert (
        passthrough[0]["metrics"]["kernel_version"]
        == "pykernel-factor-2026-08-23.1"
    )
    # 透传：无戳旧记录保持无戳（stale_kernel 判定原料，不得补造）
    plain = champion_payload(champs)
    assert "kernel_version" not in plain[0]["metrics"]
