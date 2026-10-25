import math

import numpy as np
import pytest

from app.services.stock_research_v2.metrics import compute_metrics


def test_sample_volatility_sharpe_and_geometric_return_share_explicit_method():
    values = [0.01, -0.02, 0.03]
    nav = np.cumprod(1 + np.array(values)).tolist()
    result = compute_metrics(values, nav, [0.2, 0.4], 0.)
    assert result["annualized_vol"] == pytest.approx(np.std(values, ddof=1) * math.sqrt(244))
    assert result["sharpe"] == pytest.approx(np.mean(values) / np.std(values, ddof=1) * math.sqrt(244))
    assert result["annualized_return"] == pytest.approx(nav[-1] ** (244 / 3) - 1)
    assert result["max_drawdown"] == pytest.approx(.02)
    assert result["positive_day_ratio"] == pytest.approx(2 / 3)
    assert result["average_rebalance_turnover"] == pytest.approx(.3)


def test_no_drawdown_zero_volatility_and_single_return_have_explicit_nulls():
    result = compute_metrics([0., 0.], [1., 1.], [], .03)
    assert result["sharpe"] is None and result["calmar"] is None
    assert result["annualized_vol"] == 0 and result["positive_day_ratio"] == 0
    assert result["average_rebalance_turnover"] is None
    assert result["undefined_reasons"]["sharpe"] == "zero_volatility"
    single = compute_metrics([-.01], [.99], [], 0.)
    assert single["annualized_vol"] is None
    assert single["max_drawdown"] == pytest.approx(.01)


def test_effective_annual_risk_free_return_is_converted_to_daily():
    returns = [.01, -.01]
    result = compute_metrics(returns, [1.01, .9999], [], .05)
    daily = math.expm1(math.log1p(.05) / 244)
    assert result["risk_free_daily"] == pytest.approx(daily)
    assert result["sharpe"] == pytest.approx((np.mean(returns) - daily) / np.std(returns, ddof=1) * math.sqrt(244))


@pytest.mark.parametrize("returns,nav,turnover,rf", [([], [], [], 0), ([0], [0], [], 0), ([.01], [2.], [], 0), ([float('nan')], [1.], [], 0), ([0], [1.], [-1], 0), ([0], [1.], [], -1), ([True], [1.], [], 0), ([0], [1.], [], float('inf'))])
def test_invalid_financial_inputs_fail_instead_of_returning_success(returns, nav, turnover, rf):
    with pytest.raises(ValueError): compute_metrics(returns, nav, turnover, rf)
