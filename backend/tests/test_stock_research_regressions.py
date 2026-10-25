from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.api.stock_factors import BacktestBody
from app.services.stock_factors.engine import (
    BacktestParams,
    MarketData,
    PreprocessConfig,
    preprocess,
    run_portfolio_backtest,
)


def market_fixture() -> MarketData:
    dates = pd.bdate_range("2024-01-01", "2024-04-30")
    prices = pd.DataFrame(
        {
            "600001": 100 * 1.001 ** np.arange(len(dates)),
            "600002": 100 * 1.002 ** np.arange(len(dates)),
        },
        index=dates,
    )
    return MarketData(prices, {}, {}, {}, {})


def backtest_params(factors: list[str] | None = None) -> BacktestParams:
    return BacktestParams(
        factor_ids=factors or ["ret_20d"],
        top_n=1,
        commission_rate=0,
        slippage_rate=0,
        preprocess=PreprocessConfig(winsorize="none", fillna=False),
    )


def test_stock_buy_hold_nav_matches_observed_price_ratio():
    market = market_fixture()
    result = run_portfolio_backtest(market, backtest_params())
    first_return_date = pd.Timestamp(result["nav"][0]["date"])
    final_date = pd.Timestamp(result["nav"][-1]["date"])
    entry_date = market.close.index[market.close.index.get_loc(first_return_date) - 1]
    expected_nav = market.close.loc[final_date, "600002"] / market.close.loc[entry_date, "600002"]
    assert result["nav"][-1]["nav"] == pytest.approx(expected_nav, abs=0.00005)


def test_stock_requested_missing_factor_fails_explicitly():
    result = run_portfolio_backtest(market_fixture(), backtest_params(["ret_20d", "roe"]))
    assert result.get("error"), "Missing ROE must not silently turn a two-factor request into momentum only"


def test_stock_missing_held_price_never_returns_nonfinite_nav():
    market = market_fixture()
    market.close.loc[pd.Timestamp("2024-02-29"), "600002"] = np.nan
    result = run_portfolio_backtest(market, backtest_params())
    if result.get("error"):
        return
    assert all(math.isfinite(point["nav"]) for point in result["nav"])


def test_stock_joint_neutralization_keeps_industry_residuals_zero():
    factors = pd.DataFrame(
        [[1, 2, 4, 1, 2, 4]],
        index=[pd.Timestamp("2024-01-02")],
        columns=["600001", "600002", "600003", "600004", "600005", "600006"],
    )
    market_caps = pd.DataFrame(
        [np.exp([1, 2, 3, 5, 6, 7])], index=factors.index, columns=factors.columns
    )
    industries = {
        code: "industry_one" if position < 3 else "industry_two"
        for position, code in enumerate(factors.columns)
    }
    result = preprocess(
        factors,
        PreprocessConfig(
            winsorize="none",
            standardize=False,
            fillna=False,
            industry_neutral=True,
            mktcap_neutral=True,
        ),
        industries,
        market_caps,
    )
    assert float(result.iloc[0, :3].mean()) == pytest.approx(0, abs=1e-8)
    assert float(result.iloc[0, 3:].mean()) == pytest.approx(0, abs=1e-8)


def test_stock_backtest_rejects_unsupported_frequency_and_weight():
    with pytest.raises(ValidationError):
        BacktestBody(rebalance="invalid", weight="invalid")
