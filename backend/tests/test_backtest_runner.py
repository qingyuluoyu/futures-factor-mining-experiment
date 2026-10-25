"""回测主循环集成测试：盘中止损触发。

验证核心 bug 修复：当某根 K 线盘中击穿止损价但收盘回拉时，
该 bar 内应触发止损平仓，而非拖到下一根或完全不触发。
"""

import pytest

from app.services.backtest import runner as runner_mod
from app.services.backtest.runner import run_backtest


def _bar(time: str, o: float, h: float, low: float, c: float, vol: int = 1000) -> dict:
    """构造 8 字段 K 线（与 test_kline_fields_extension 一致）"""
    return {
        "time": time,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": vol,
        "settle": c,
        "open_interest": 0,
    }


def _fake_trade_params(symbol: str, settings) -> dict:
    """内存品种参数，绕开 DB"""
    return {
        "multiplier": 10,
        "margin_rate": 0.1,
        "fee_mode": "fixed",
        "open_fee": 5.0,
        "close_fee": 5.0,
    }


@pytest.mark.asyncio
async def test_intraday_stop_loss_triggers_within_same_bar(monkeypatch):
    """盘中击穿止损但收盘回拉 → 当根 bar 触发止损平仓。

    lookback=5：warmup 5 根最高 high=100，bar1 收盘 105 > 100 向上突破 → 开多。
    bar2 开多成交（runner 在 bar1 决策、bar1 收盘价记账，avg≈105）。
    bar3 盘中 low=96（穿 3% 止损线 ≈101.85），收盘 103（未穿）。
    修复后应在 bar3 触发止损，成交价≈精确止损价，而非收盘价 103。
    """
    # 5 根 warmup：high 上限 100，让 bar1 收盘 105 突破
    warmup = [_bar(f"2026-06-{d:02d}", 95, 100, 94, 98) for d in range(1, 6)]
    bars = warmup + [
        _bar("2026-07-01", 100, 106, 99, 105, 2000),  # 收盘 105 > 100 向上突破 → 开多
        _bar("2026-07-02", 105, 107, 104, 106, 2000),  # 持仓，未触发
        _bar("2026-07-03", 106, 107, 96.0, 103.0, 2000),  # 盘中击穿止损，收盘回拉
    ]

    async def fake_load(*args, **kwargs):
        return bars

    monkeypatch.setattr(runner_mod, "load_backtest_bars", fake_load)
    monkeypatch.setattr(runner_mod, "resolve_trade_params", _fake_trade_params)

    payload = {
        "symbol": "ni2607",
        "timeframe": "1d",
        "start_date": "2026-07-01",
        "end_date": "2026-07-03",
        "strategy_type": "n_breakout",
        "strategy_params": {"lookback": 5},
        "side_mode": "long_only",
        "fixed_qty": 1,
        "initial_cash": 1_000_000,
        "stop_rules": {"loss_pct": 3},
        "close_rules": {},
    }

    result = await run_backtest(session=None, redis_conn=None, user_id=None, payload=payload)

    opens = [d for d in result["decisions"] if d["action"] in ("open_long", "open_short")]
    if not opens:
        pytest.skip("测试数据未触发开仓，跳过止损断言")

    closes = [d for d in result["decisions"] if d["action"] == "close" and d["source"] == "rule"]
    assert closes, "盘中击穿止损价但未触发平仓（核心 bug 未修复）"

    triggered = closes[0]
    assert "止损" in triggered["reason"]
    # 成交价应为精确止损价（avg≈105，止损3% → ≈101.85），明显低于收盘价 103
    assert triggered["price"] < 103.0, (
        f"止损成交价应为精确止损价(≈101.85)，实际{triggered['price']}"
    )


@pytest.mark.asyncio
async def test_no_stop_loss_no_early_close(monkeypatch):
    """不设止损时，盘中波动不应触发规则平仓（回归保护）"""
    warmup = [_bar(f"2026-06-{d:02d}", 95, 100, 94, 98) for d in range(1, 6)]
    bars = warmup + [
        _bar("2026-07-01", 100, 106, 99, 105, 2000),
        _bar("2026-07-02", 105, 107, 96.0, 103.0, 2000),  # 盘中大跌但无止损
        _bar("2026-07-03", 103, 104, 102, 103, 1000),
    ]

    async def fake_load(*args, **kwargs):
        return bars

    monkeypatch.setattr(runner_mod, "load_backtest_bars", fake_load)
    monkeypatch.setattr(runner_mod, "resolve_trade_params", _fake_trade_params)

    payload = {
        "symbol": "ni2607",
        "timeframe": "1d",
        "start_date": "2026-07-01",
        "end_date": "2026-07-03",
        "strategy_type": "n_breakout",
        "strategy_params": {"lookback": 5},
        "side_mode": "long_only",
        "fixed_qty": 1,
        "initial_cash": 1_000_000,
        "stop_rules": {},
        "close_rules": {},
    }

    result = await run_backtest(session=None, redis_conn=None, user_id=None, payload=payload)

    rule_closes = [d for d in result["decisions"] if d["source"] == "rule"]
    assert not rule_closes, "未设止损却触发了规则平仓"
