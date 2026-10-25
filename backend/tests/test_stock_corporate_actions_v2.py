from decimal import Decimal as D

from tests.test_stock_execution_v2 import bar
from tests.test_stock_portfolio_v2 import request,inputs


def event(kind='net_cash_dividend',**changes):
    from app.schemas.stock_portfolio_v2 import CorporateAction
    base={'event_id':'qa-event','instrument_id':'000001.SZ','kind':kind,'record_date':'2024-01-03',
        'effective_date':'2024-01-04','settlement_date':'2024-01-05','evidence_reference':'qa:event',
        'cash_per_share':'1'}
    base.update(changes)
    return CorporateAction.model_validate(base)


def test_cash_dividend_entitlement_receivable_and_settlement_preserve_economic_nav():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel=inputs(events=[event()])
    changed=[bar(str(x.trade_date),x.instrument_id,open='9',high='10',low='8',close='9',down_limit='8',
        adj_factor='1.1') if str(x.trade_date)>='2024-01-04' and x.instrument_id=='000001.SZ' else x for x in panel.bars]
    result=simulate_portfolio(request(),panel.model_copy(update={'bars':changed}))
    assert result.status=='completed'
    assert result.daily[2].receivables==D('900')
    assert result.daily[2].cash==D('994.91') and result.daily[3].cash==D('1894.91')
    assert result.daily[2].equity==result.daily[1].equity==result.daily[3].equity
    assert len([x for x in result.ledger if x['kind']=='corporate_action'])==2


def test_share_distribution_keeps_basis_and_new_shares_locked_until_explicit_settlement():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    evt=event('share_distribution',cash_per_share=None,share_multiplier='2')
    panel=inputs(events=[evt])
    changed=[bar(str(x.trade_date),x.instrument_id,open='5',high='6',low='4',close='5',up_limit='6',down_limit='4',
        adj_factor='2') if str(x.trade_date)>='2024-01-04' and x.instrument_id=='000001.SZ' else x for x in panel.bars]
    result=simulate_portfolio(request(),panel.model_copy(update={'bars':changed}))
    assert result.status=='completed'
    assert result.daily[2].receivables==D('4500') and result.daily[2].position_value==D('4500')
    assert sum(x.quantity for x in result.daily[3].lots)==1800
    assert sum(x.cost_basis for x in result.daily[3].lots)==D('9005.09')
    assert result.daily[3].equity==result.daily[1].equity


def test_delisting_needs_explicit_cash_event_and_does_not_disappear_with_dynamic_universe():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    evt=event('delisting_cash',cash_per_share='7')
    panel=inputs(events=[evt])
    changed=[x.model_copy(update={'status':'delisted','close':None}) if str(x.trade_date)>='2024-01-04'
        and x.instrument_id=='000001.SZ' else x for x in panel.bars]
    result=simulate_portfolio(request(),panel.model_copy(update={'bars':changed}))
    assert result.status=='completed' and result.daily[2].position_value==0
    assert result.daily[2].receivables==D('6300') and result.daily[3].cash==D('7294.91')
    no_event=simulate_portfolio(request(),panel.model_copy(update={'bars':changed,'events':[]}))
    assert no_event.status=='partially_completed'


def test_unsupported_event_stops_instead_of_assuming_zero():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result=simulate_portfolio(request(),inputs(events=[event('unsupported')]))
    assert result.status=='partially_completed' and len(result.daily)==2
    assert any('unsupported_corporate_action' in x for x in result.blocking_reasons)


def test_authoritative_delisting_event_settles_even_without_subsequent_market_bars():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel=inputs(events=[event('delisting_cash',cash_per_share='7')])
    remaining=[x for x in panel.bars if x.instrument_id!='000001.SZ' or str(x.trade_date)<'2024-01-04']
    result=simulate_portfolio(request(),panel.model_copy(update={'bars':remaining}))
    assert result.status=='completed' and result.daily[-1].equity==D('7294.91')
