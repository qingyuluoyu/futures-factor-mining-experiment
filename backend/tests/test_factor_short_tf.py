"""短周期因子挖掘修正测试 —— 年化基数、成本模型、换手守卫

锁住三条不可回归的性质：
1. 年化基数必须随周期变化（1m 远大于 1d），且日线仍为 243；
2. 期货成本必须含 tick 滑点，不能退回股票口径的固定 3bp；
3. 换手守卫不得再把正常短线换手（0.3~0.8）判成退化。
"""

from __future__ import annotations

import numpy as np

from app.services.factor_lab.scoring.cost import (
    DEFAULT_SLIPPAGE_TICKS,
    fee_rate_one_way,
    turnover_cost_rate,
)
from app.services.factor_lab.scoring.evaluate import evaluate_factor
from app.services.factor_lab.scoring.periods import (
    TRADING_DAYS_PER_YEAR,
    bars_per_year,
    distinct_trading_days,
)
from app.services.trading_hours import trading_day_of_bar_time


def _minute_bars(days: int, per_day: int) -> list[dict]:
    """合成分钟 bars：每个交易日 per_day 根，时间落在日盘 09:00 起"""
    bars: list[dict] = []
    for d in range(days):
        for i in range(per_day):
            hour = 9 + i // 60
            minute = i % 60
            bars.append(
                {
                    "time": f"2026-03-{d + 2:02d} {hour:02d}:{minute:02d}:00",
                    "open": 100.0,
                    "high": 100.5,
                    "low": 99.5,
                    "close": 100.0,
                    "volume": 1000,
                }
            )
    return bars


# ── 交易日归属 ────────────────────────────────────────────────


def test_trading_day_of_bar_time_day_session():
    """日盘 bar 归属当日"""
    assert trading_day_of_bar_time("2026-03-02 14:30:00") == "2026-03-02"


def test_trading_day_of_bar_time_night_session():
    """夜盘 21:00 之后归属次一交易日"""
    assert trading_day_of_bar_time("2026-03-02 21:30:00") == "2026-03-03"


def test_trading_day_of_bar_time_daily_label():
    """日线标签本身就是交易日，原样返回"""
    assert trading_day_of_bar_time("2026-03-02") == "2026-03-02"


def test_trading_day_of_bar_time_invalid():
    """无法解析返回空串"""
    assert trading_day_of_bar_time("") == ""
    assert trading_day_of_bar_time("bad") == ""


# ── 年化基数 ──────────────────────────────────────────────────


def test_distinct_trading_days_counts_sessions():
    bars = _minute_bars(days=5, per_day=10)
    assert distinct_trading_days(bars) == 5


def test_bars_per_year_daily_is_trading_days():
    """日线年化基数必须保持 243"""
    assert bars_per_year([], "1d") == TRADING_DAYS_PER_YEAR


def test_bars_per_year_measures_minute_bars():
    """分钟线按实测日均 bar 数 × 243"""
    bars = _minute_bars(days=6, per_day=120)
    assert bars_per_year(bars, "1m") == 120 * TRADING_DAYS_PER_YEAR


def test_bars_per_year_falls_back_when_sample_short():
    """样本不足 3 天时按典型交易时段折算，而非退回日线"""
    bars = _minute_bars(days=1, per_day=5)
    assert bars_per_year(bars, "5m") > TRADING_DAYS_PER_YEAR


def test_bars_per_year_monotonic_across_timeframes():
    """周期越短，年化基数越大；且都不小于日线"""
    short = bars_per_year([], "1m")
    mid = bars_per_year([], "15m")
    long_tf = bars_per_year([], "60m")
    assert short > mid > long_tf >= TRADING_DAYS_PER_YEAR


def test_bars_per_year_never_below_daily():
    """未知周期兜底为日线基数"""
    assert bars_per_year([], "3m") == TRADING_DAYS_PER_YEAR


# ── 年化随基数缩放（核心回归点）────────────────────────────────


def test_annualization_scales_with_periods():
    """同一因子，年化基数放大 N 倍则年化收益放大 N 倍

    这是短周期年率被低估的直接原因：原实现固定 periods=243。
    """
    rng = np.random.RandomState(3)
    close = 100.0 + np.cumsum(rng.randn(400) * 0.3)
    factor = np.tanh(rng.randn(400))
    daily = evaluate_factor(factor, close, cost=0.0, periods=243)
    minute = evaluate_factor(factor, close, cost=0.0, periods=243 * 100)
    assert minute["periods"] == 243 * 100
    assert np.isclose(minute["ann_ret"], daily["ann_ret"] * 100)


def test_composite_reflects_periods():
    """基数不同 → composite 不同（收益项不再被压平）"""
    rng = np.random.RandomState(5)
    close = 100.0 + np.cumsum(np.abs(rng.randn(300)) * 0.2)
    factor = np.tanh(np.linspace(-1.0, 1.0, 300))
    low = evaluate_factor(factor, close, cost=0.0, periods=243)
    high = evaluate_factor(factor, close, cost=0.0, periods=243 * 50)
    assert high["composite"] != low["composite"]


# ── 成本模型 ──────────────────────────────────────────────────


def test_turnover_cost_includes_slippage():
    """总成本必须严格大于纯手续费（含 1 tick 滑点）"""
    fee_only = fee_rate_one_way("rb2610", 3000.0)
    total = turnover_cost_rate("rb2610", 3000.0, DEFAULT_SLIPPAGE_TICKS)
    assert total > fee_only


def test_turnover_cost_scales_with_slippage_ticks():
    """滑点跳数翻倍，滑点部分翻倍"""
    fee_only = fee_rate_one_way("rb2610", 3000.0)
    one = turnover_cost_rate("rb2610", 3000.0, 1.0) - fee_only
    two = turnover_cost_rate("rb2610", 3000.0, 2.0) - fee_only
    assert np.isclose(two, one * 2.0)


def test_turnover_cost_handles_zero_price():
    """价格缺失时不炸，退化为纯手续费"""
    total = turnover_cost_rate("rb2610", 0.0, DEFAULT_SLIPPAGE_TICKS)
    assert total == fee_rate_one_way("rb2610", 0.0)


# ── 换手守卫 ──────────────────────────────────────────────────


def test_turnover_guard_allows_short_term_churn():
    """平均换手 0.6 的短线仓位不应被判退化

    构造：每 3 根 bar 满仓反手一次 → |Δpos|≈1.93，平均换手≈0.64。
    原实现 1-turnover*5 会给出 1-3.2=-2.2（钳到地板分 -1.0），
    系统性淘汰这类正常短线因子；修正后应为中性 0.0。
    """
    close = np.linspace(100.0, 110.0, 210)
    factor = np.array(
        [2.0 if (i // 3) % 2 == 0 else -2.0 for i in range(210)],
        dtype=float,
    )
    metrics = evaluate_factor(factor, close, cost=0.0, periods=243 * 100)
    assert 0.3 < metrics["avg_turnover"] < 1.0
    # 旧公式在该换手下必然触底，新守卫应判为中性
    assert metrics["turnover_q"] == 0.0


def test_turnover_guard_flags_never_trading():
    """恒空仓因子仍被判无效"""
    close = np.linspace(100.0, 110.0, 100)
    factor = np.zeros(100)
    metrics = evaluate_factor(factor, close, cost=0.0, periods=243)
    assert metrics["turnover_q"] == -1.0


def test_turnover_guard_flags_every_bar_flip():
    """每根满仓反手（换手 2.0）判为退化"""
    close = np.linspace(100.0, 110.0, 100)
    factor = np.array([5.0 if i % 2 == 0 else -5.0 for i in range(100)])
    metrics = evaluate_factor(factor, close, cost=0.0, periods=243)
    assert metrics["avg_turnover"] > 1.0
    assert metrics["turnover_q"] < 0.0
