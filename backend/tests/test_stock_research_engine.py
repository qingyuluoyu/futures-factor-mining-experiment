from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.services.stock_factors.engine import (
    BacktestParams,
    MarketData,
    PreprocessConfig,
    _build_market_data,
    analyze_factor,
    build_factor_matrix,
    forward_return,
    preprocess,
    run_portfolio_backtest,
)


def research_market(count: int = 2) -> MarketData:
    dates = pd.bdate_range("2024-01-01", "2024-04-30")
    prices = pd.DataFrame(
        {
            f"{600001 + position}": 100 * (1.001 + position * 0.001) ** np.arange(len(dates))
            for position in range(count)
        },
        index=dates,
    )
    valuation = pd.DataFrame(
        np.tile(np.arange(1, count + 1, dtype=float), (len(dates), 1)),
        index=dates,
        columns=prices.columns,
    )
    return MarketData(prices, {"pb": valuation}, {}, {}, {})


def research_params(**overrides) -> BacktestParams:
    return BacktestParams(
        **{
            "factor_ids": ["ret_20d"],
            "top_n": 1,
            "commission_rate": 0,
            "slippage_rate": 0,
            "preprocess": PreprocessConfig(winsorize="none", standardize=False, fillna=False),
            **overrides,
        }
    )


def test_nav_follows_each_observed_close_and_retains_drawdown():
    market = research_market()
    entry_date = pd.Timestamp("2024-01-31")
    market.close.loc[pd.Timestamp("2024-02-02"), "600002"] *= 0.5
    result = run_portfolio_backtest(market, research_params())
    points = {point["date"]: point["nav"] for point in result["nav"]}
    for observed_date in ("2024-02-01", "2024-02-02", "2024-02-05"):
        expected = market.close.loc[observed_date, "600002"] / market.close.loc[entry_date, "600002"]
        assert points[observed_date] == pytest.approx(expected, abs=1e-7)
    assert result["metrics"]["max_drawdown_pct"] > 49


def test_equal_weight_portfolio_keeps_units_between_rebalances():
    market = research_market()
    result = run_portfolio_backtest(market, research_params(top_n=2))
    expected = (
        market.close.loc["2024-02-20"] / market.close.loc["2024-01-31"]
    ).mean()
    observed = next(point["nav"] for point in result["nav"] if point["date"] == "2024-02-20")
    assert observed == pytest.approx(expected, abs=1e-7)


def test_execution_uses_previous_session_signal():
    market = research_market()
    market.valuation["pb"].loc["2024-01-31", ["600001", "600002"]] = [100, 1]
    result = run_portfolio_backtest(market, research_params(factor_ids=["pb"]))
    assert result["trades"][0]["code"] == "600001"
    assert result["trades"][0]["signal_date"] == "2024-01-30"
    assert all(trade["signal_date"] < trade["date"] for trade in result["trades"])


def test_terminal_date_never_generates_unearned_rebalance_or_fees():
    result = run_portfolio_backtest(research_market(), research_params())
    assert all(trade["date"] < result["nav"][-1]["date"] for trade in result["trades"])
    assert result["rebalance_count"] == 3


@pytest.mark.parametrize("missing_date", ["2024-02-15", "2024-02-29"])
def test_any_missing_held_close_fails_without_fabricating_return(missing_date):
    market = research_market()
    market.close.loc[missing_date, "600002"] = np.nan
    result = run_portfolio_backtest(market, research_params())
    assert result["error_code"] == "missing_held_price"
    assert result["status_code"] == 409
    assert "nav" not in result


def test_entirely_null_requested_factor_fails_before_imputation():
    market = research_market()
    market.financial["roe"] = market.close * np.nan
    result = run_portfolio_backtest(market, research_params(factor_ids=["ret_20d", "roe"]))
    assert result["error_code"] == "missing_factor_data"
    assert "roe" in result["error"]


def test_market_cap_weight_never_silently_falls_back_to_equal_weight():
    result = run_portfolio_backtest(research_market(), research_params(weight="mktcap"))
    assert result["error_code"] == "missing_market_cap"


def test_entry_fees_are_self_financed_and_charged_once():
    market = research_market()
    result = run_portfolio_backtest(market, research_params(commission_rate=0.01))
    entry_date = pd.Timestamp(result["trades"][0]["date"])
    final_date = pd.Timestamp(result["nav"][-1]["date"])
    expected = market.close.loc[final_date, "600002"] / market.close.loc[entry_date, "600002"] / 1.01
    assert result["nav"][-1]["nav"] == pytest.approx(expected, abs=1e-7)
    assert result["total_cost"] == pytest.approx(0.01 / 1.01, abs=1e-9)


def test_turnover_uses_drifted_holdings_and_records_sales():
    market = research_market()
    market.valuation["pb"].loc["2024-02-28":, ["600001", "600002"]] = [100, 1]
    result = run_portfolio_backtest(market, research_params(factor_ids=["pb"]))
    switched = [trade for trade in result["trades"] if trade["date"] == "2024-02-29"]
    assert {trade["action"] for trade in switched} == {"卖出", "买入"}
    assert {trade["code"] for trade in switched} == {"600001", "600002"}


def test_geometric_annual_return_uses_compounded_nav():
    result = run_portfolio_backtest(research_market(), research_params())
    expected = ((1.002 ** 244) - 1) * 100
    assert result["metrics"]["annualized_return_pct"] == pytest.approx(expected, abs=0.0001)


def test_joint_neutralization_is_orthogonal_on_the_common_sample():
    market = research_market(6)
    factors = market.close.iloc[:1].copy()
    factors.iloc[0] = [1, 2, 4, 1, 2, 4]
    caps = factors.copy()
    caps.iloc[0] = np.exp([1, 2, 3, 5, 6, 7])
    industries = {code: "one" if position < 3 else "two" for position, code in enumerate(factors.columns)}
    result = preprocess(
        factors,
        PreprocessConfig(winsorize="none", standardize=False, fillna=False, industry_neutral=True, mktcap_neutral=True),
        industries,
        caps,
    )
    assert np.dot(result.iloc[0], np.log(caps.iloc[0])) == pytest.approx(0, abs=1e-10)
    assert result.iloc[0].tolist() == pytest.approx([1 / 6, -1 / 3, 1 / 6] * 2, abs=1e-10)


def test_volatility_never_forward_fills_a_missing_price():
    market = research_market()
    market.close.loc["2024-02-15", "600002"] = np.nan
    factor = build_factor_matrix(market, "vol_20d")
    assert pd.isna(factor.loc["2024-02-16", "600002"])


def test_forward_return_starts_after_the_next_session_execution():
    market = research_market()
    market.close.loc["2024-01-31", "600002"] *= 1.2
    observed = forward_return(market, 5).loc["2024-01-30", "600002"]
    expected = market.close.loc["2024-02-07", "600002"] / market.close.loc["2024-01-31", "600002"] - 1
    assert observed == pytest.approx(expected, abs=1e-12)


def test_announcement_date_only_data_is_usable_on_the_next_session():
    market = _build_market_data(
        [("600001", date(2024, 1, day), 100.0) for day in (2, 3, 4)],
        [],
        [("600001", date(2023, 12, 31), date(2024, 1, 3), 15.0, 20.0, 5.0, 6.0)],
        [],
    )
    assert pd.isna(market.financial["roe"].loc["2024-01-03", "600001"])
    assert market.financial["roe"].loc["2024-01-04", "600001"] == 15


def test_analysis_nav_is_independent_of_horizon_and_future_price_suffix():
    market = research_market(10)
    config = PreprocessConfig(winsorize="none", fillna=False)
    shorter = analyze_factor(market, "pb", config, horizon=5, groups=2)
    longer = analyze_factor(market, "pb", config, horizon=20, groups=2)
    assert shorter["long_short_nav"] == longer["long_short_nav"]
    mutated = research_market(10)
    mutated.close.loc["2024-03-01":] *= 0.7
    changed = analyze_factor(mutated, "pb", config, horizon=5, groups=2)
    original_prefix = [point for point in shorter["long_short_nav"] if point["date"] < "2024-03-01"]
    changed_prefix = [point for point in changed["long_short_nav"] if point["date"] < "2024-03-01"]
    assert original_prefix == changed_prefix
    first = shorter["long_short_nav"][0]
    expected = 1 + np.mean([0.001, 0.002, 0.003, 0.004, 0.005]) - np.mean([0.006, 0.007, 0.008, 0.009, 0.010])
    assert first["date"] == "2024-01-03"
    assert first["nav"] == pytest.approx(expected, abs=1e-9)


def test_results_are_finite_json_and_explicit_about_research_limits():
    result = run_portfolio_backtest(research_market(), research_params())
    assert result["method_version"]
    assert result["validation_status"] == "research_only"
    assert any("停牌" in item for item in result["validation_limits"])
    assert any("复权" in item for item in result["validation_limits"])
    json.dumps(result, allow_nan=False)
