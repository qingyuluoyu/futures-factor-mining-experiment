from datetime import date
from decimal import Decimal as D

import pytest


def bar(day='2024-01-03', instrument='000001.SZ', **changes):
    from app.schemas.stock_portfolio_v2 import ExecutionBar
    return ExecutionBar.model_validate({'trade_date':day, 'instrument_id':instrument,
        'open':'10', 'high':'11', 'low':'9', 'close':'10', 'volume':'100000', 'volume_unit':'share',
        'adj_factor':'1', 'status':'trading', 'up_limit':'11', 'down_limit':'9',
        'evidence_reference':'qa:raw-and-status', **changes})


def order(side='buy', quantity=100, **changes):
    from app.schemas.stock_portfolio_v2 import PortfolioOrder
    return PortfolioOrder.model_validate({'order_id':'qa-order', 'instrument_id':'000001.SZ',
        'side':side, 'quantity':quantity, 'decision_date':'2024-01-02', 'execution_date':'2024-01-03', **changes})


def match(item=None, daily=None, lots=None, cash=D('10000'), **kwargs):
    from app.schemas.stock_portfolio_v2 import CostSettings
    from app.services.stock_research_v2.execution import execute_order
    return execute_order(item or order(), daily or bar(), lots or [], cash,
        CostSettings(slippage_rate='0'), **kwargs)


def test_fill_conserves_cash_and_cost_basis():
    result = match()
    assert result.status == 'filled'
    assert result.cash == D('8994.99') and result.cash_delta == D('-1005.01')
    assert result.fees.total == D('5.01') and result.lots[0].cost_basis == D('1005.01')
    sold = match(order('sell'), lots=result.lots)
    assert sold.status == 'rejected' and sold.reason == 't_plus_one_or_insufficient_position'
    sold = match(order('sell', decision_date='2024-01-03', execution_date='2024-01-04'),
        bar('2024-01-04', open='10.50'), result.lots, result.cash)
    assert sold.cash == D('10039.45') and sold.realized_pnl == D('39.45') and sold.lots == []


@pytest.mark.parametrize('changes,reason', [({'status':'suspended'},'suspended'),
    ({'status':None},'missing_status'), ({'adj_factor':None},'missing_adj_factor'),
    ({'up_limit':None},'missing_limit'), ({'open':None},'missing_raw_price'),
    ({'open':'11'},'limit_up_at_open'), ({'volume':'500'},'liquidity_limit')])
def test_explicit_rejections_do_not_mutate_state(changes, reason):
    result = match(daily=bar(**changes))
    assert result.status == 'rejected' and result.reason == reason
    assert result.cash == D('10000') and result.lots == []


def test_exact_volume_conversion_and_aggregate_participation():
    result = match(daily=bar(volume='10', volume_unit='lot_100_shares'))
    assert result.status == 'filled'
    assert match(daily=bar(volume='10', volume_unit='lot_100_shares'), used_volume=1).reason == 'liquidity_limit'
    with pytest.raises(ValueError, match='integral'):
        bar(volume='.001', volume_unit='lot_100_shares')


def test_order_dates_mainboard_lots_and_cash_are_enforced():
    assert match(cash=D('1000')).reason == 'insufficient_cash'
    assert match(order(quantity=99)).reason == 'invalid_lot_size'
    assert match(order(instrument_id='688001.SH'), bar(instrument='688001.SH')).reason == 'unsupported_instrument'
    with pytest.raises(ValueError, match='execution_date'):
        order(decision_date='2024-01-03')


def test_odd_lot_sale_must_include_all_remainder_and_t_plus_one():
    from app.schemas.stock_portfolio_v2 import PositionLot
    lot = PositionLot(instrument_id='000001.SZ', quantity=253, cost_basis='2530', acquired_on='2024-01-01')
    assert match(order('sell', 53), lots=[lot]).status == 'filled'
    assert match(order('sell', 3), lots=[lot]).reason == 'invalid_odd_lot_sale'
    assert match(order('sell', 153), lots=[lot]).lots[0].quantity == 100


def test_slippage_is_bounded_by_official_limits_and_never_uses_intraday_path():
    from app.schemas.stock_portfolio_v2 import CostSettings
    from app.services.stock_research_v2.execution import execute_order
    result = execute_order(order(), bar(open='10.99'), [], D('10000'), CostSettings(slippage_rate='.01'))
    assert result.price == D('11.00')


def test_copied_typed_models_cannot_bypass_quantity_and_participation_contract():
    from app.schemas.stock_portfolio_v2 import CostSettings
    from app.services.stock_research_v2.execution import execute_order
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        execute_order(order().model_copy(update={'quantity':-100}),bar(),[],D('10000'),CostSettings())
    with pytest.raises(ValidationError):
        execute_order(order(),bar(),[],D('10000'),CostSettings().model_copy(update={'participation_rate':D('1')}))
