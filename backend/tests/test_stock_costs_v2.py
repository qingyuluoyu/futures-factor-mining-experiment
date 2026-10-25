from datetime import date
from decimal import Decimal as D

import pytest


def test_date_effective_cost_breakdown_and_money_rounding():
    from app.schemas.stock_portfolio_v2 import CostSettings
    from app.services.stock_research_v2.costs import calculate_costs
    settings = CostSettings(slippage_rate='0')
    old = calculate_costs(D('1000'), 'sell', date(2023, 8, 25), settings)
    new = calculate_costs(D('1000'), 'sell', date(2023, 8, 28), settings)
    buy = calculate_costs(D('1000'), 'buy', date(2023, 8, 28), settings)
    assert (old.commission, old.transfer_fee, old.stamp_tax, old.total) == (D('5'), D('.01'), D('1'), D('6.01'))
    assert new.total == D('5.51') and buy.total == D('5.01')
    assert calculate_costs(D('16683.34'), 'buy', date(2024, 1, 1), settings).commission == D('5.01')


@pytest.mark.parametrize('day', [date(2022, 6, 30), date(2026, 10, 26)])
def test_unknown_fee_period_fails_closed(day):
    from app.schemas.stock_portfolio_v2 import CostSettings
    from app.services.stock_research_v2.costs import calculate_costs
    with pytest.raises(ValueError, match='fee_period_unverified'):
        calculate_costs(D('1000'), 'sell', day, CostSettings())
