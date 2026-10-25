"""平仓标准扩展测试 —— 技术指标死叉/金叉平仓 + 因子阈值平仓

口径与 strategies/macd_cross.py 等策略层一致：
死叉 = 前一根 快线≥慢线 且 最新一根 快线<慢线；金叉对称。
方向语义：持多仓遇死叉平多、持空仓遇金叉平空。
"""

from __future__ import annotations

from typing import Any

from app.services.ai.tools.calc import calc_kdj, calc_macd, calc_sma
from app.services.ai_trading.core.exit_signals import (
    check_indicator_exit_rules,
    evaluate_factor_exit_rules,
    factor_entry_suppressed,
    normalize_close_rules_exits,
    normalize_factor_exit,
    normalize_indicator_exits,
)
from app.services.ai_trading.strategies.factor import (
    _latest_position,
    compute_factor_signal,
    normalize_factor_params,
)


def _bars_from_closes(closes: list[float]) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(closes):
        out.append(
            {
                "time": f"t{i}",
                "open": c - 0.2,
                "high": c + 0.6,
                "low": c - 0.6,
                "close": c,
                "volume": 1,
            }
        )
    return out


def _long_pos(qty: int = 2) -> dict:
    return {"direction": "long", "quantity": qty, "available_quantity": qty}


def _short_pos(qty: int = 3) -> dict:
    return {"direction": "short", "quantity": qty, "available_quantity": qty}


def _bars_with_cross_at_last(kind: str, params: dict | None = None) -> list[dict]:
    """搜索一段先涨后跌（或先跌后涨）的序列，使指定指标恰好在最后一根死叉（或金叉）。

    自校验 fixture：直接用 calc_* 判定穿越成立才返回，保证测试确定性。
    """
    params = params or {}
    if kind == "ma_cross":
        fast = int(params.get("fast_period") or 5)
        slow = int(params.get("slow_period") or 20)
        for k in range(1, 80):
            closes = [100.0 + i * 0.5 for i in range(60)] + [
                100.0 + 59 * 0.5 - 0.5 * i for i in range(1, k + 1)
            ]
            f = calc_sma(closes, fast)
            s = calc_sma(closes, slow)
            if None in (f[-1], f[-2], s[-1], s[-2]):
                continue
            if f[-2] >= s[-2] and f[-1] < s[-1]:
                return _bars_from_closes(closes)
        raise AssertionError("未找到均线死叉序列")
    if kind == "kdj_cross":
        n = int(params.get("n_period") or 9)
        for k in range(1, 80):
            closes = [100.0 + i * 0.8 for i in range(60)] + [
                100.0 + 59 * 0.8 - 0.9 * i for i in range(1, k + 1)
            ]
            bars = _bars_from_closes(closes)
            highs = [float(b["high"]) for b in bars]
            lows = [float(b["low"]) for b in bars]
            pts = calc_kdj(highs, lows, closes, n, 3, 3)
            k0, d0 = pts[-2]["k"], pts[-2]["d"]
            k1, d1 = pts[-1]["k"], pts[-1]["d"]
            if None in (k0, d0, k1, d1):
                continue
            if k0 >= d0 and k1 < d1:
                return bars
        raise AssertionError("未找到KDJ死叉序列")
    # macd_cross 默认 12/26/9
    for k in range(1, 120):
        closes = [100.0 + i * 0.8 for i in range(70)] + [
            100.0 + 69 * 0.8 - 0.6 * i for i in range(1, k + 1)
        ]
        pts = calc_macd(closes, 12, 26, 9)
        d0, e0 = pts[-2]["dif"], pts[-2]["dea"]
        d1, e1 = pts[-1]["dif"], pts[-1]["dea"]
        if None in (d0, e0, d1, e1):
            continue
        if d0 >= e0 and d1 < e1:
            return _bars_from_closes(closes)
    raise AssertionError("未找到MACD死叉序列")


def _bars_with_golden_at_last() -> list[dict]:
    """先跌后涨，MACD 恰好在最后一根金叉（用于平空）"""
    for k in range(1, 120):
        closes = [100.0 - i * 0.8 for i in range(70)] + [
            100.0 - 69 * 0.8 + 0.6 * i for i in range(1, k + 1)
        ]
        pts = calc_macd(closes, 12, 26, 9)
        d0, e0 = pts[-2]["dif"], pts[-2]["dea"]
        d1, e1 = pts[-1]["dif"], pts[-1]["dea"]
        if None in (d0, e0, d1, e1):
            continue
        if d0 <= e0 and d1 > e1:
            return _bars_from_closes(closes)
    raise AssertionError("未找到MACD金叉序列")


# ---------- 技术指标平仓 ----------


def test_macd_death_closes_long() -> None:
    bars = _bars_with_cross_at_last("macd_cross")
    hit = check_indicator_exit_rules([{"kind": "macd_cross"}], bars, _long_pos())
    assert hit is not None
    assert hit["action"] == "close"
    assert hit["trigger_type"] == "indicator_close"
    assert hit["quantity"] == 2
    assert "下穿" in hit["reason"] and "平多" in hit["reason"]


def test_macd_death_does_not_close_short() -> None:
    """死叉只平多：空仓遇死叉不动（等金叉）"""
    bars = _bars_with_cross_at_last("macd_cross")
    assert check_indicator_exit_rules([{"kind": "macd_cross"}], bars, _short_pos()) is None


def test_macd_golden_closes_short() -> None:
    bars = _bars_with_golden_at_last()
    hit = check_indicator_exit_rules([{"kind": "macd_cross"}], bars, _short_pos())
    assert hit is not None
    assert hit["action"] == "close"
    assert hit["trigger_type"] == "indicator_close"
    assert "上穿DEA" in hit["reason"] and "平空" in hit["reason"]


def test_macd_golden_does_not_close_long() -> None:
    bars = _bars_with_golden_at_last()
    assert check_indicator_exit_rules([{"kind": "macd_cross"}], bars, _long_pos()) is None


def test_no_cross_no_trigger() -> None:
    """单调上涨无穿越：不触发（且持多仓不触发金叉平多）"""
    bars = _bars_from_closes([100.0 + i * 0.5 for i in range(80)])
    assert check_indicator_exit_rules([{"kind": "macd_cross"}], bars, _long_pos()) is None
    assert check_indicator_exit_rules([{"kind": "macd_cross"}], bars, _short_pos()) is None


def test_ma_death_closes_long_with_params() -> None:
    params = {"fast_period": 5, "slow_period": 20, "ma_type": "sma"}
    bars = _bars_with_cross_at_last("ma_cross", params)
    hit = check_indicator_exit_rules(
        [{"kind": "ma_cross", "params": params}], bars, _long_pos()
    )
    assert hit is not None
    assert hit["trigger_type"] == "indicator_close"
    assert "下穿" in hit["reason"] and "平多" in hit["reason"]


def test_kdj_death_closes_long() -> None:
    bars = _bars_with_cross_at_last("kdj_cross")
    hit = check_indicator_exit_rules([{"kind": "kdj_cross"}], bars, _long_pos())
    assert hit is not None
    assert hit["trigger_type"] == "indicator_close"
    assert "下穿" in hit["reason"] and "平多" in hit["reason"]


def test_insufficient_bars_skips_condition() -> None:
    """K线不足该指标要求：跳过该条件而不是报错"""
    bars = _bars_from_closes([100.0 + i * 0.5 for i in range(30)])
    hit = check_indicator_exit_rules(
        [{"kind": "macd_cross"}, {"kind": "ma_cross", "params": {"slow_period": 60}}],
        bars,
        _long_pos(),
    )
    assert hit is None


def test_no_position_no_trigger() -> None:
    bars = _bars_with_cross_at_last("macd_cross")
    assert check_indicator_exit_rules([{"kind": "macd_cross"}], bars, None) is None
    assert (
        check_indicator_exit_rules([{"kind": "macd_cross"}], bars, {"direction": "long"})
        is None
    )


def test_multiple_conditions_first_hit_wins() -> None:
    """多条件命中任意一个即平仓"""
    bars = _bars_with_cross_at_last("macd_cross")
    exits = [
        {"kind": "ma_cross", "params": {"fast_period": 5, "slow_period": 20}},
        {"kind": "macd_cross"},
    ]
    hit = check_indicator_exit_rules(exits, bars, _long_pos())
    assert hit is not None
    assert hit["context"]["exit_kind"] == "macd_cross"


# ---------- 规范化 ----------


def test_normalize_indicator_exits_whitelist_and_dedupe() -> None:
    raw = [
        {"kind": "macd_cross"},
        {"kind": "macd_cross"},  # 重复 → 去重
        {"kind": "ma_cross", "params": {"fast_period": 10, "slow_period": 30}},
        {"kind": "rsi"},  # 白名单外 → 丢弃
        "junk",
    ]
    out = normalize_indicator_exits(raw)
    kinds = [x["kind"] for x in out]
    assert kinds == ["macd_cross", "ma_cross"]
    assert out[0]["params"]["fast_period"] == 12  # 缺省参数补默认


def test_normalize_indicator_exits_caps_and_kdj_zone_stripped() -> None:
    raw = [{"kind": "kdj_cross", "params": {"use_zone": True, "oversold": 20}}] * 6
    out = normalize_indicator_exits(raw)
    assert len(out) == 1  # 完全重复去重后只剩 1 条
    assert "use_zone" not in out[0]["params"]  # 平仓不做超买超卖过滤
    # 不同参数不去重，但超过 3 条截断
    many = [
        {"kind": "ma_cross", "params": {"fast_period": f, "slow_period": 60}}
        for f in (3, 5, 10, 15, 20)
    ]
    assert len(normalize_indicator_exits(many)) == 3


def test_normalize_factor_exit_clamps() -> None:
    # decay 模式钳到 ≤0.29（必须低于开仓线 0.3）
    cfg = normalize_factor_exit({"mode": "decay", "long_threshold": 0.5})
    assert cfg is not None
    assert cfg["long_threshold"] == 0.29
    # reach 模式钳到 ≥0.35
    cfg = normalize_factor_exit({"mode": "reach", "long_threshold": 0.2})
    assert cfg is not None
    assert cfg["long_threshold"] == 0.35
    # 负数按绝对值处理
    cfg = normalize_factor_exit({"mode": "decay", "long_threshold": -0.15, "short_threshold": 0.1})
    assert cfg is not None
    assert cfg["long_threshold"] == 0.15
    assert cfg["short_threshold"] == 0.1
    # 非法 mode 回退 decay；非法输入返回 None
    assert normalize_factor_exit({"mode": "xx"}) == {"mode": "decay", "long_threshold": 0.15, "short_threshold": 0.15}
    assert normalize_factor_exit(None) is None
    assert normalize_factor_exit("junk") is None


def test_normalize_close_rules_exits_drops_empty() -> None:
    out = normalize_close_rules_exits(
        {"pnl_pct": 5, "indicator_exits": [], "factor_exit": {}}
    )
    assert "indicator_exits" not in out
    assert "factor_exit" not in out
    assert out["pnl_pct"] == 5
    out2 = normalize_close_rules_exits(
        {"indicator_exits": [{"kind": "macd_cross"}], "factor_exit": {"mode": "reach"}}
    )
    assert out2["indicator_exits"] == [{"kind": "macd_cross", "params": {"fast_period": 12, "slow_period": 26, "signal_period": 9}}]
    assert out2["factor_exit"]["mode"] == "reach"
    assert out2["factor_exit"]["long_threshold"] == 0.8


# ---------- 因子阈值平仓 ----------


def test_factor_exit_decay_long_and_short() -> None:
    cfg = {"mode": "decay", "long_threshold": 0.15, "short_threshold": 0.15}
    # 多仓：pos 跌破 +0.15 → 平多
    hit = evaluate_factor_exit_rules(cfg, 0.10, _long_pos())
    assert hit is not None and hit["trigger_type"] == "factor_exit"
    assert "平多" in hit["reason"]
    # 多仓：pos 仍在阈值上方 → 不动
    assert evaluate_factor_exit_rules(cfg, 0.20, _long_pos()) is None
    # 空仓：pos 回升破 -0.15 → 平空
    hit = evaluate_factor_exit_rules(cfg, -0.10, _short_pos())
    assert hit is not None and "平空" in hit["reason"]
    # 空仓：pos 仍低于阈值 → 不动
    assert evaluate_factor_exit_rules(cfg, -0.30, _short_pos()) is None


def test_factor_exit_reach_long_and_short() -> None:
    cfg = {"mode": "reach", "long_threshold": 0.8, "short_threshold": 0.8}
    hit = evaluate_factor_exit_rules(cfg, 0.9, _long_pos())
    assert hit is not None and "平多" in hit["reason"]
    assert evaluate_factor_exit_rules(cfg, 0.5, _long_pos()) is None
    hit = evaluate_factor_exit_rules(cfg, -0.9, _short_pos())
    assert hit is not None and "平空" in hit["reason"]
    assert evaluate_factor_exit_rules(cfg, -0.5, _short_pos()) is None


def test_factor_exit_reach_suppresses_entry() -> None:
    reach = {"mode": "reach", "long_threshold": 0.8, "short_threshold": 0.8}
    decay = {"mode": "decay", "long_threshold": 0.15, "short_threshold": 0.15}
    assert factor_entry_suppressed(reach, 0.85) is True
    assert factor_entry_suppressed(reach, -0.9) is True
    assert factor_entry_suppressed(reach, 0.5) is False
    assert factor_entry_suppressed(decay, 0.9) is False  # 衰减模式不抑制入场
    assert factor_entry_suppressed(None, 0.99) is False


def _rising_bars(n: int = 160) -> list[dict]:
    return _bars_from_closes([100.0 + i * 0.4 for i in range(n)])


def test_compute_factor_signal_reach_blocks_open() -> None:
    """reach 模式：因子已达离场阈值时抑制新开仓（防开完立刻平的抖动）"""
    bars = _rising_bars()
    p = normalize_factor_params({"factor_tokens": [8]})
    pos = _latest_position(bars, p["factor_tokens"])
    assert pos is not None and abs(pos) >= 0.35  # 趋势序列因子强度足够（自校验）

    base = compute_factor_signal(bars, p, "both", None)
    assert base["action"] in ("open_long", "open_short")  # 无出场配置：正常开仓

    reach = {"mode": "reach", "long_threshold": 0.35, "short_threshold": 0.35}
    sig = compute_factor_signal(bars, p, "both", None, exit_rules=reach)
    assert sig["action"] == "hold"
    assert sig["signal"] == "factor_exit_suppressed"
    assert "不追" in sig["reason"]

    decay = {"mode": "decay", "long_threshold": 0.15, "short_threshold": 0.15}
    sig2 = compute_factor_signal(bars, p, "both", None, exit_rules=decay)
    assert sig2["action"] == base["action"]  # 衰减模式不影响入场


def test_compute_factor_signal_exit_rules_ignored_on_close() -> None:
    """reach 抑制只挡新开仓：反向持仓的平仓信号不受影响"""
    bars = _rising_bars()
    p = normalize_factor_params({"factor_tokens": [8]})
    pos = _latest_position(bars, p["factor_tokens"])
    assert pos is not None and abs(pos) > 0.3
    reach = {"mode": "reach", "long_threshold": 0.35, "short_threshold": 0.35}
    # 因子偏向与持仓相反 → 转向平仓，抑制逻辑不拦截平仓
    opposite = _long_pos() if pos < 0 else _short_pos()
    sig = compute_factor_signal(bars, p, "both", opposite, exit_rules=reach)
    assert sig["action"] == "close"
