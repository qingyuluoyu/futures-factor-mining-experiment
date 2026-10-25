"""回测硬规则（止损/止盈/最长持仓）盘中触发测试。

覆盖核心 bug：止损/止盈此前只看收盘价，盘中触及阈值但收盘回拉时
不触发，导致回测偏差。修复后用盘中 high/low 触发，并以精确触发价记账。
"""

import pytest

from app.services.backtest.signals import apply_hard_rules


def _long(avg: float = 100.0, qty: int = 2) -> dict:
    return {"direction": "long", "quantity": qty, "avg_price": avg}


def _short(avg: float = 100.0, qty: int = 2) -> dict:
    return {"direction": "short", "quantity": qty, "avg_price": avg}


# ---------- 百分比止损：盘中 low 触发 ----------


def test_long_stop_loss_triggered_by_intraday_low():
    """多头：盘中 low 跌破止损线（即使收盘回拉）也必须触发"""
    # avg=100，止损3% → 精确止损价 97。盘中 low=96 已穿线，收盘 99 未穿
    res = apply_hard_rules(
        _long(),
        last_price=99.0,
        stop_rules={"loss_pct": 3},
        close_rules={},
        fixed_qty=2,
        bar_high=100.0,
        bar_low=96.0,
    )
    assert res is not None
    assert res["action"] == "close"
    assert res["quantity"] == 2
    # 精确止损价：100 * (1 - 3/100) = 97
    assert res["trigger_price"] == pytest.approx(97.0)


def test_long_stop_loss_NOT_triggered_when_close_only_fails():
    """核心 bug 回归保护：若只看收盘价（99→浮亏1%）不会触发，
    修复后必须靠 low 触发。本例断言结果非 None。"""
    res = apply_hard_rules(
        _long(),
        last_price=99.0,
        stop_rules={"loss_pct": 3},
        close_rules={},
        fixed_qty=2,
        bar_high=100.0,
        bar_low=96.0,
    )
    assert res is not None  # 旧逻辑只看 close 会返回 None


def test_short_stop_loss_triggered_by_intraday_high():
    """空头：盘中 high 涨过止损线即触发"""
    # avg=100，止损3% → 精确止损价 103。盘中 high=104 已穿线，收盘 101 未穿
    res = apply_hard_rules(
        _short(),
        last_price=101.0,
        stop_rules={"loss_pct": 3},
        close_rules={},
        fixed_qty=2,
        bar_high=104.0,
        bar_low=100.0,
    )
    assert res is not None
    assert res["action"] == "close"
    # 空头精确止损价：100 * (1 + 3/100) = 103
    assert res["trigger_price"] == pytest.approx(103.0)


def test_stop_loss_not_triggered_below_threshold():
    """high/low 均未达阈值 → 不触发"""
    res = apply_hard_rules(
        _long(),
        last_price=99.0,
        stop_rules={"loss_pct": 3},
        close_rules={},
        fixed_qty=2,
        bar_high=101.0,
        bar_low=98.0,  # 浮亏 2%，未到 3%
    )
    assert res is None


def test_stop_loss_none_when_no_rule():
    """无止损规则 → 不触发"""
    res = apply_hard_rules(
        _long(),
        last_price=90.0,
        stop_rules={},
        close_rules={},
        fixed_qty=2,
        bar_high=100.0,
        bar_low=80.0,
    )
    assert res is None


# ---------- 百分比止盈：盘中 high/low 触发 ----------


def test_long_take_profit_triggered_by_intraday_high():
    """多头：盘中 high 涨过止盈线即触发"""
    # avg=100，止盈5% → 精确止盈价 105。盘中 high=106，收盘 102
    res = apply_hard_rules(
        _long(),
        last_price=102.0,
        stop_rules={},
        close_rules={"pnl_pct": 5},
        fixed_qty=2,
        bar_high=106.0,
        bar_low=100.0,
    )
    assert res is not None
    assert res["action"] == "close"
    assert res["trigger_price"] == pytest.approx(105.0)


def test_short_take_profit_triggered_by_intraday_low():
    """空头：盘中 low 跌过止盈线即触发"""
    # avg=100，止盈5% → 精确止盈价 95。盘中 low=94，收盘 98
    res = apply_hard_rules(
        _short(),
        last_price=98.0,
        stop_rules={},
        close_rules={"pnl_pct": 5},
        fixed_qty=2,
        bar_high=100.0,
        bar_low=94.0,
    )
    assert res is not None
    assert res["trigger_price"] == pytest.approx(95.0)


def test_take_profit_not_triggered_below_threshold():
    res = apply_hard_rules(
        _long(),
        last_price=103.0,
        stop_rules={},
        close_rules={"pnl_pct": 5},
        fixed_qty=2,
        bar_high=104.0,  # 浮盈4%，未到5%
        bar_low=100.0,
    )
    assert res is None


# ---------- 防御：极值价缺失/异常回退收盘价 ----------


def test_fallback_to_close_when_high_low_missing():
    """bar_high/bar_low 为 0 → 回退收盘价判定"""
    # 收盘价 96 触发 3% 止损，但 low 缺失（=0），回退后用 96 判定
    res = apply_hard_rules(
        _long(),
        last_price=96.0,
        stop_rules={"loss_pct": 3},
        close_rules={},
        fixed_qty=2,
        bar_high=0.0,
        bar_low=0.0,
    )
    assert res is not None
    assert res["trigger_price"] == pytest.approx(97.0)


def test_fallback_when_high_below_close():
    """异常 high < close → 回退到 close，不误判"""
    res = apply_hard_rules(
        _long(),
        last_price=98.0,
        stop_rules={"loss_pct": 3},
        close_rules={},
        fixed_qty=2,
        bar_high=50.0,  # 异常
        bar_low=97.5,  # 浮亏2.5%，未到3%
    )
    assert res is None


# ---------- 金额止损 / 总收益止盈（与实盘对齐） ----------


def test_loss_amount_triggered_by_intraday():
    """金额止损用盘中探测价算浮亏金额"""
    # avg=100，qty=2，金额止损=20 → 浮亏金额<=-20 触发
    # 多头 low=88：浮亏金额 (88-100)*2 = -24 <= -20 → 触发
    res = apply_hard_rules(
        _long(),
        last_price=95.0,
        stop_rules={"loss_amount": 20},
        close_rules={},
        fixed_qty=2,
        bar_high=100.0,
        bar_low=88.0,
    )
    assert res is not None
    assert res["action"] == "close"
    # 精确价：avg - loss_amt/qty = 100 - 20/2 = 90
    assert res["trigger_price"] == pytest.approx(90.0)


def test_total_pnl_pct_triggered():
    """总收益止盈用 cash_delta_pct（收盘口径）"""
    res = apply_hard_rules(
        _long(),
        last_price=100.0,
        stop_rules={},
        close_rules={"total_pnl_pct": 10},
        fixed_qty=2,
        bar_high=100.0,
        bar_low=100.0,
        cash_delta_pct=12.0,
    )
    assert res is not None
    assert res["action"] == "close"


# ---------- 最长持仓优先 ----------


def test_max_hold_days_priority():
    res = apply_hard_rules(
        _long(),
        last_price=200.0,  # 即便大幅盈利也不该先止盈
        stop_rules={},
        close_rules={"pnl_pct": 5},
        fixed_qty=2,
        max_hold_days=5,
        hold_days=6.0,
        bar_high=200.0,
        bar_low=200.0,
    )
    assert res is not None
    assert "持仓已" in res["reason"]


def test_no_position_returns_none():
    assert apply_hard_rules(None, 100, {}, {}, 2, bar_high=100, bar_low=100) is None
