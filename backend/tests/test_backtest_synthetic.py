"""AI 合成 K 线生成 + step 推进器 + finish 指标"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.services.backtest.synthetic import (
    BARS_PER_DAY,
    MarketScript,
    account_to_snapshot,
    finish_replay,
    render_bars_from_script,
    resolve_ref_price,
    snapshot_to_account,
    step_decision,
    _coerce_script,
    _default_script,
)


# ------------------------------------------------------------------
# 渲染不变量
# ------------------------------------------------------------------

@pytest.mark.parametrize("timeframe", ["1d", "60m", "30m", "15m", "5m", "1m"])
def test_render_ohlc_invariants(timeframe: str):
    """所有周期生成的 K 线都必须满足 OHLC 合法性"""
    script = MarketScript(regime="volatile_range", trend_strength=0.5,
                          volatility=0.015, jumps=[{"at": 0.5, "direction": "up", "size": 0.03}])
    bars = render_bars_from_script(
        script, symbol="pp", timeframe=timeframe, num_bars=50,
        ref_price=7500.0, amplitude=0.015, seed=42,
    )
    assert len(bars) == 50
    prev_close = 7500.0
    for b in bars:
        # OHLC 合法
        assert b["low"] <= b["open"] <= b["high"], b
        assert b["low"] <= b["close"] <= b["high"], b
        assert b["open"] > 0 and b["close"] > 0 and b["volume"] > 0
        # 单根涨跌幅不超过 15%（防极端）
        assert 0.85 * prev_close <= b["close"] <= 1.15 * prev_close, b
        prev_close = b["close"]


def test_render_time_format():
    """日线 time=YYYY-MM-DD；分钟线 time=YYYY-MM-DD HH:MM:SS"""
    daily = render_bars_from_script(
        MarketScript(), symbol="rb", timeframe="1d", num_bars=3,
        ref_price=3600, amplitude=0.015, seed=1,
    )
    assert "T" not in daily[0]["time"]
    assert len(daily[0]["time"]) == 10  # YYYY-MM-DD

    minute = render_bars_from_script(
        MarketScript(), symbol="rb", timeframe="15m", num_bars=3,
        ref_price=3600, amplitude=0.01, seed=1,
    )
    assert len(minute[0]["time"]) == 19  # YYYY-MM-DD HH:MM:SS


def test_render_seed_reproducible():
    """相同 seed 产出相同序列"""
    s = MarketScript(regime="bull", trend_strength=0.6, volatility=0.02)
    a = render_bars_from_script(s, symbol="cu", timeframe="1d", num_bars=20,
                                 ref_price=70000, amplitude=0.015, seed=123)
    b = render_bars_from_script(s, symbol="cu", timeframe="1d", num_bars=20,
                                 ref_price=70000, amplitude=0.015, seed=123)
    assert a == b


# ------------------------------------------------------------------
# 剧本收敛
# ------------------------------------------------------------------

def test_coerce_script_clamps_out_of_range():
    """LLM 输出越界值时应被收敛到合法范围"""
    bad = {
        "regime": "INVALID", "trend_strength": 5.0, "volatility": -0.1,
        "drift_bps": 999, "jumps": [{"at": 1.5, "direction": "sideways", "size": 0.5}],
        "volume_pattern": "nope",
    }
    s = _coerce_script(bad, "1d")
    assert s.regime == "volatile_range"  # 非法 regime 回退
    assert 0.0 <= s.trend_strength <= 1.0
    assert s.volatility > 0
    assert -50 <= s.drift_bps <= 50
    assert all(0 <= j["at"] <= 1 for j in s.jumps)
    assert all(j["direction"] in ("up", "down") for j in s.jumps)
    assert s.volume_pattern == "trending"


def test_default_script_valid():
    s = _default_script("1d")
    assert s.regime in {"bull", "bear", "sideways", "bull_then_sideways",
                        "bear_then_bounce", "volatile_range"}


# ------------------------------------------------------------------
# 参考价解析
# ------------------------------------------------------------------

def test_resolve_ref_price():
    assert resolve_ref_price("pp", 8000) == 8000  # 显式传入优先
    assert resolve_ref_price("RB2510", None) == 3600.0  # 去数字后缀
    assert resolve_ref_price("", None) == 5000.0  # 通用默认


def test_bars_per_day_table():
    assert BARS_PER_DAY["1d"] == 1
    assert BARS_PER_DAY["1m"] == 240
    assert BARS_PER_DAY["15m"] == 16


# ------------------------------------------------------------------
# 账户快照往返
# ------------------------------------------------------------------

def test_account_snapshot_roundtrip():
    from app.services.backtest.account import VirtualAccount
    acct = VirtualAccount(cash=1_000_000, multiplier=10, margin_rate=0.1,
                          fee_mode="fixed", open_fee=5, close_fee=5)
    acct.apply("open_long", 2, 100, "2026-01-01", "开多")
    snap = account_to_snapshot(acct, 105)
    assert snap["side"] == "long"
    assert snap["qty"] == 2

    restored = snapshot_to_account(
        snap, multiplier=10, margin_rate=0.1, fee_mode="fixed",
        open_fee=5, close_fee=5, initial_cash=1_000_000,
    )
    assert restored.side == "long"
    assert restored.qty == 2
    assert restored.cash == pytest.approx(acct.cash)


# ------------------------------------------------------------------
# step 决策（无 LLM，用量化策略）
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_decision_quant_n_breakout(monkeypatch):
    """N 日突破策略在合成数据上能产生决策（不依赖 LLM/DB）"""
    # 造一段先跌后涨的序列，触发突破
    script = MarketScript(regime="bull", trend_strength=0.8, volatility=0.02)
    bars = render_bars_from_script(
        script, symbol="rb", timeframe="1d", num_bars=30,
        ref_price=3600, amplitude=0.02, seed=7,
    )
    # 让最后几根明显上涨以触发突破
    for i in range(25, 30):
        bars[i]["high"] = bars[i - 1]["high"] * 1.02
        bars[i]["close"] = bars[i - 1]["close"] * 1.015

    # mock resolve_trade_params 避免依赖品种库
    async def fake_step(*args, **kwargs):
        return await step_decision.__wrapped__(**kwargs) if hasattr(step_decision, "__wrapped__") else None

    # 直接 monkeypatch 掉 resolve_trade_params（step_decision 内部 import）
    import app.services.backtest.synthetic as synth_mod
    orig = synth_mod.resolve_trade_params

    def fake_resolve(symbol, settings):
        return {
            "code": symbol or "DEFAULT", "name": "test",
            "multiplier": 10, "margin_rate": 0.1, "fee_mode": "fixed",
            "open_fee": 5.0, "close_fee": 5.0,
        }
    monkeypatch.setattr(synth_mod, "resolve_trade_params", fake_resolve)
    try:
        result = await step_decision(
            session=None, user_id=uuid4(),
            bars_so_far=bars, account_snapshot=None,
            strategy_type="n_breakout", strategy_params={"n": 20},
            side_mode="both", fixed_qty=1, initial_cash=1_000_000,
            risk_style="balanced", custom_prompt_enabled=False,
            custom_prompt=None, close_rules={"pnl_pct": None, "session_close": False},
            stop_rules={"loss_pct": None}, max_hold_days=10,
            model_row_id=None, symbol="rb", symbol_name="螺纹钢",
            timeframe="1d",
        )
    finally:
        synth_mod.resolve_trade_params = orig

    assert "action" in result
    assert "account_snapshot" in result
    assert "equity_point" in result
    # 账户快照可序列化
    snap = result["account_snapshot"]
    assert snap["equity"] > 0


# ------------------------------------------------------------------
# finish 指标
# ------------------------------------------------------------------

def test_finish_replay_returns_run_backtest_shape():
    """finish_replay 返回结构必须与 run_backtest 一致，可被 BacktestReportView 复用"""
    from app.services.backtest.account import VirtualAccount
    script = MarketScript(regime="bull", trend_strength=0.5)
    bars = render_bars_from_script(
        script, symbol="pp", timeframe="1d", num_bars=15,
        ref_price=7500, amplitude=0.015, seed=3,
    )
    acct = VirtualAccount(cash=1_000_000, multiplier=5, margin_rate=0.09,
                          fee_mode="fixed", open_fee=1.0, close_fee=1.0)
    acct.apply("open_long", 1, bars[2]["close"], bars[2]["time"], "开多")
    acct.apply("close", 1, bars[5]["close"], bars[5]["time"], "平多")
    snap = account_to_snapshot(acct, bars[-1]["close"])
    eq = [{"time": b["time"], "equity": snap["equity"],
           "cash": snap["cash"], "unrealized": 0.0} for b in bars]

    report = finish_replay(
        equity_curve=eq, trades=acct.trades, account_snapshot=snap,
        initial_cash=1_000_000, bars=bars, strategy_type="n_breakout", ai_calls=0,
    )
    # 与 run_backtest 返回结构对齐
    assert report["status"] == "completed"
    for k in ("metrics", "equity_curve", "bars", "trades", "decisions", "message", "config"):
        assert k in report, f"缺字段 {k}"
    m = report["metrics"]
    for k in ("initial_cash", "final_equity", "total_return", "total_return_pct",
              "max_drawdown_pct", "win_rate", "trade_count", "profit_factor"):
        assert k in m, f"metrics 缺字段 {k}"
    assert m["trade_count"] == 2
    assert len(report["bars"]) == 15
    assert "AI 合成 K 线" in report["message"]
