from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from app.api.stock_factors import list_factors
from app.services.stock_factors.engine import BacktestParams, MarketData, PreprocessConfig, analyze_factor, run_portfolio_backtest


def value_market(factor_id):
    dates = pd.bdate_range("2024-01-01", "2024-03-15")
    codes = [f"{600001 + position}" for position in range(17)]
    close = pd.DataFrame({code: 100 * (1.001 + position * 0.0002) ** np.arange(len(dates)) for position, code in enumerate(codes)}, index=dates)
    values = pd.DataFrame(np.tile([-10, 0, np.nan, np.inf, -np.inf, *range(1, 13)], (len(dates), 1)), index=dates, columns=codes)
    caps = pd.DataFrame(np.tile(np.exp(np.linspace(1, 3, len(codes)) ** 2), (len(dates), 1)), index=dates, columns=codes)
    return MarketData(close, {factor_id: values, "total_mv": caps}, {}, {code: "one" if position % 2 else "two" for position, code in enumerate(codes)}, {})


def portfolio_params(factor_id, config):
    return BacktestParams(factor_ids=[factor_id], top_n=12, rebalance="week", commission_rate=0, slippage_rate=0, preprocess=config)


@pytest.mark.parametrize("factor_id", ["pe_ttm", "pb"])
@pytest.mark.parametrize("fillna", [False, True])
@pytest.mark.parametrize("winsorize", ["none", "mad", "3sigma"])
@pytest.mark.parametrize("neutral", [False, True])
@pytest.mark.parametrize("standardize", [False, True])
def test_value_ineligible_samples_cannot_reenter_portfolio_or_analysis(factor_id, fillna, winsorize, neutral, standardize):
    market = value_market(factor_id)
    config = PreprocessConfig(fillna=fillna, winsorize=winsorize, standardize=standardize, industry_neutral=neutral, mktcap_neutral=neutral)
    result = run_portfolio_backtest(market, portfolio_params(factor_id, config))
    assert "error" not in result, result
    eligible_codes = set(market.close.columns[5:])
    assert {trade["code"] for trade in result["trades"]} == eligible_codes
    valid_only = replace(market, close=market.close.iloc[:, 5:])
    expected = analyze_factor(valid_only, factor_id, config, horizon=5, groups=2)
    actual = analyze_factor(market, factor_id, config, horizon=5, groups=2)
    assert "error" not in actual, actual
    for key in ("ic", "quantile_returns", "long_short_nav"):
        assert actual[key] == expected[key], key


@pytest.mark.parametrize("factor_id", ["pe_ttm", "pb"])
def test_policy_counts_explain_raw_and_processed_samples(factor_id):
    market = value_market(factor_id)
    for result in (run_portfolio_backtest(market, portfolio_params(factor_id, PreprocessConfig())), analyze_factor(market, factor_id, groups=2, horizon=5)):
        policy = result["factor_policies"][0]
        assert result["method_version"] == "stock-research-2026-10-19.v5"
        assert result["neutralization_policy"]["requires_full_column_rank"] is True
        assert result["neutralization_policy"]["min_residual_degrees_of_freedom"] == 1
        assert policy["policy_version"] == "stock-factor-domain-v1"
        assert policy["eligibility_rule"] == "finite_strictly_positive"
        assert policy["missing_value_policy"] == "exclude_without_imputation"
        count = len(market.close)
        assert policy["counts"] == {"observations": count * 17, "raw_eligible": count * 12, "excluded_nonpositive": count * 2, "excluded_nonfinite": count * 2, "missing": count, "postprocess_eligible": count * 12, "imputed": 0}
        assert policy["count_unit"] == "date_security_observations"


@pytest.mark.parametrize("factor_id", ["revenue_yoy", "netprofit_yoy", "ret_20d"])
def test_negative_nonvalue_factor_and_generic_missing_fill_remain_valid(factor_id):
    market = value_market("pb")
    if factor_id == "ret_20d":
        market.close = 10000 / market.close
    else:
        market.financial[factor_id] = -market.valuation["pb"].abs().replace([np.inf, -np.inf], np.nan)
    result = run_portfolio_backtest(market, BacktestParams(factor_ids=[factor_id], top_n=16, rebalance="week", preprocess=PreprocessConfig(winsorize="none", standardize=False)))
    assert "error" not in result, result
    assert any(trade["factor_value"] < 0 for trade in result["trades"])
    policy = result["factor_policies"][0]
    assert policy["eligibility_rule"] == "finite"
    assert policy["counts"]["excluded_nonpositive"] == 0
    if factor_id != "ret_20d":
        assert policy["counts"]["imputed"] > 0


@pytest.mark.parametrize("factor_id", ["pe_ttm", "pb"])
def test_all_ineligible_values_fail_explicitly(factor_id):
    market = value_market(factor_id)
    market.valuation[factor_id].iloc[:, 5:] = 0
    for result in (analyze_factor(market, factor_id), run_portfolio_backtest(market, portfolio_params(factor_id, PreprocessConfig()))):
        assert result["error_code"] == "missing_factor_data"
        assert result["factor_policies"][0]["counts"]["raw_eligible"] == 0


@pytest.mark.asyncio
async def test_catalog_exposes_policy_and_current_method_version():
    specs = {spec["id"]: spec for spec in await list_factors(None)}
    for factor_id in ("pe_ttm", "pb"):
        assert specs[factor_id]["eligibility_rule"] == "finite_strictly_positive"
        assert specs[factor_id]["missing_value_policy"] == "exclude_without_imputation"
        assert specs[factor_id]["policy_version"] == "stock-factor-domain-v1"
        assert specs[factor_id]["method_version"] == "stock-research-2026-10-19.v5"
    assert specs["revenue_yoy"]["eligibility_rule"] == "finite"


@pytest.mark.parametrize("factor_id", ["pe_ttm", "pb"])
def test_valid_positive_values_keep_lowest_value_first(factor_id):
    market = value_market(factor_id)
    params = portfolio_params(factor_id, PreprocessConfig(winsorize="none", standardize=False, fillna=True))
    params.top_n = 1
    result = run_portfolio_backtest(market, params)
    assert {trade["code"] for trade in result["trades"]} == {market.close.columns[5]}


def test_multifactor_composite_preserves_intersection_of_value_eligibility():
    market = value_market("pb")
    market.valuation["pe_ttm"] = market.valuation["pb"].copy()
    market.valuation["pe_ttm"].iloc[:, 5] = -1
    params = portfolio_params("pb", PreprocessConfig())
    params.factor_ids = ["pb", "pe_ttm"]
    params.top_n = 11
    result = run_portfolio_backtest(market, params)
    assert {trade["code"] for trade in result["trades"]} == set(market.close.columns[6:])
    assert [policy["factor_id"] for policy in result["factor_policies"]] == ["pb", "pe_ttm"]


def test_requested_window_without_eligible_value_fails_before_neutralization():
    market = value_market("pb")
    market.valuation["pb"].loc["2024-02-01":] = 0
    market.valuation.pop("total_mv")
    params = portfolio_params("pb", PreprocessConfig(mktcap_neutral=True))
    params.start_date = "2024-02-01"
    result = run_portfolio_backtest(market, params)
    assert result["error_code"] == "missing_factor_data"
    policy = result["factor_policies"][0]
    assert policy["start_date"] == "2024-02-01"
    assert policy["counts"]["observations"] == len(market.close.loc["2024-02-01":]) * 17
    assert policy["counts"]["raw_eligible"] == 0
