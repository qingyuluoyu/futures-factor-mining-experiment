from decimal import Decimal as D
from uuid import UUID

import pytest

from tests.test_stock_execution_v2 import bar


def request(**changes):
    from app.schemas.stock_portfolio_v2 import PortfolioRequest
    return PortfolioRequest.model_validate({'scope':{'owner_id':str(UUID(int=1)), 'trading_mode':'virtual'},
        'dataset':{'dataset_id':str(UUID(int=2)), 'data_version':'a'*64, 'qualification':'observation_only'},
        'pool_version':'qa-pool-v1', 'start':'2024-01-02', 'end':'2024-01-05', 'top_n':1,
        'rebalance':'explicit', 'initial_cash':'10000', 'qa_only':True,
        'costs':{'slippage_rate':'0'}, **changes})


def inputs(*, missing_evidence=None, future_changes=None, signals=None, events=None):
    from app.schemas.stock_portfolio_v2 import PortfolioInputs
    dates = ['2024-01-02','2024-01-03','2024-01-04','2024-01-05']
    evidence = {name:'qa:complete-coverage' for name in ['calendar_reference','raw_prices_reference',
        'status_and_limits_reference','corporate_actions_reference','delistings_reference','historical_universe_reference']}
    evidence.update(coverage_start=dates[0],coverage_end=dates[-1])
    if missing_evidence:
        evidence[missing_evidence] = None
    return PortfolioInputs.model_validate({'calendar':dates, 'signals':signals if signals is not None else [
        {'decision_date':dates[0], 'signal_version':'qa-score-v1', 'candidates':[
            {'instrument_id':'000001.SZ','score':'2','marketcap':'100'},
            {'instrument_id':'600000.SH','score':'1','marketcap':'300'}]}],
        'bars':[bar(day,instrument, **((future_changes or {}) if day == dates[1] and instrument == '000001.SZ' else {}))
            for day in dates for instrument in ['000001.SZ','600000.SH']], 'evidence':evidence,'events':events or []})


def test_connected_signal_to_orders_fees_daily_equity_and_metrics():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(request(), inputs())
    assert result.status == 'completed' and result.validation_status == 'qa_only'
    assert len(result.daily) == 4 and len(result.executions) == 1
    fill = result.executions[0]
    assert str(fill.order.decision_date) == '2024-01-02' and str(fill.order.execution_date) == '2024-01-03'
    assert fill.order.quantity == 900 and fill.cash == D('994.91')
    assert result.daily[0].equity == D('10000') and result.daily[1].equity == D('9994.91')
    assert result.metrics['total_return'] == pytest.approx(-.000509)
    for daily in result.daily:
        assert daily.equity == daily.cash + daily.position_value + daily.receivables
    assert {x['kind'] for x in result.ledger} >= {'cash','position','fee'}


def test_future_tradability_never_changes_decision_targets_or_replaces_selected_stock():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    normal = simulate_portfolio(request(), inputs())
    future = simulate_portfolio(request(), inputs(future_changes={'status':'suspended'}))
    assert normal.targets == future.targets
    assert future.executions[0].reason == 'suspended'
    assert all(x.equity == D('10000') for x in future.daily)
    assert len(future.executions) == 1


@pytest.mark.parametrize('missing', ['calendar_reference','raw_prices_reference','status_and_limits_reference',
    'corporate_actions_reference','delistings_reference','historical_universe_reference'])
def test_missing_global_evidence_produces_no_certified_or_fake_curve(missing):
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(request(), inputs(missing_evidence=missing))
    assert result.status == 'not_qualified' and result.daily == [] and result.metrics is None
    assert 'missing_'+missing in result.blocking_reasons


def test_observation_dataset_does_not_gain_production_qualification_from_evidence_fields():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(request(qa_only=False), inputs())
    assert result.status == 'not_qualified' and 'dataset_not_backtest_ready' in result.blocking_reasons


def test_holding_missing_valuation_stops_prefix_without_zero_or_forward_fill():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel = inputs()
    changed = [x.model_copy(update={'close':None}) if str(x.trade_date)=='2024-01-04' and x.instrument_id=='000001.SZ' else x for x in panel.bars]
    result = simulate_portfolio(request(), panel.model_copy(update={'bars':changed}))
    assert result.status == 'partially_completed' and len(result.daily)==2
    assert result.metrics is None and 'missing_holding_valuation:000001.SZ:2024-01-04' in result.blocking_reasons


def test_unknown_factor_change_blocks_account_before_next_execution():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel = inputs()
    changed = [x.model_copy(update={'adj_factor':D('2')}) if str(x.trade_date)>='2024-01-04' and x.instrument_id=='000001.SZ' else x for x in panel.bars]
    result = simulate_portfolio(request(), panel.model_copy(update={'bars':changed}))
    assert result.status == 'partially_completed' and len(result.daily)==2
    assert any('unexplained_factor_change' in reason for reason in result.blocking_reasons)


@pytest.mark.parametrize('weight,expected', [('equal',[D('.5'),D('.5')]),('mktcap',[D('.25'),D('.75')]),
    ('factor',[D(2)/D(3),D(1)/D(3)])])
def test_three_weight_schemes_use_decision_inputs(weight, expected):
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result = simulate_portfolio(request(weight=weight,top_n=2), inputs())
    assert [D(x['weight']) for x in result.targets] == expected


def test_low_direction_changes_ranking_and_missing_selected_marketcap_is_not_reselected():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel=inputs()
    low=panel.signals[0].model_copy(update={'direction':'low'})
    result=simulate_portfolio(request(),panel.model_copy(update={'signals':[low]}))
    assert result.targets[0]['instrument_id']=='600000.SH'
    missing=panel.signals[0].candidates[0].model_copy(update={'marketcap':None})
    snap=panel.signals[0].model_copy(update={'candidates':[missing,panel.signals[0].candidates[1]]})
    result=simulate_portfolio(request(weight='mktcap'),panel.model_copy(update={'signals':[snap]}))
    assert result.status=='not_qualified' and result.targets==[]


def test_deterministic_artifact_saves_full_ledger(tmp_path):
    from app.services.stock_research_v2.portfolio import simulate_portfolio, save_portfolio_result
    from app.services.stock_research_v2.artifacts import ArtifactStore
    result=simulate_portfolio(request(),inputs())
    store=ArtifactStore(tmp_path)
    a=save_portfolio_result(result,store)
    b=save_portfolio_result(result,store)
    assert a==b
    # Artifact ownership is enforced by the caller's existing archive registration/read path.
    assert a.byte_size > 1000


def test_failed_execution_day_does_not_publish_partial_orders_or_ledger():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel=inputs()
    # A new close decision has invalid cap inputs after today's valid opening fill.
    from app.schemas.stock_portfolio_v2 import SignalSnapshot
    missing=SignalSnapshot(decision_date='2024-01-03',signal_version='qa',
        candidates=[{'instrument_id':'000001.SZ','score':'1'}])
    result=simulate_portfolio(request(weight='mktcap'),panel.model_copy(update={'signals':panel.signals+[missing]}))
    assert result.status=='partially_completed' and len(result.daily)==1
    assert result.executions==[] and result.ledger==[]


def test_overnight_factor_change_rejects_original_order_without_reshuffling():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    result=simulate_portfolio(request(),inputs(future_changes={'adj_factor':'2'}))
    assert result.executions[0].status=='rejected'
    assert result.executions[0].reason=='corporate_action_between_decision_and_execution'
    assert result.targets[0]['instrument_id']=='000001.SZ'
    assert all(x.equity==D('10000') for x in result.daily)


@pytest.mark.parametrize('cadence,expected', [('week',['2024-01-30','2024-02-05']),('month',['2024-01-30','2024-02-01'])])
def test_week_month_use_first_explicit_calendar_session_per_period(cadence,expected):
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    from app.schemas.stock_portfolio_v2 import PortfolioInputs
    dates=['2024-01-30','2024-01-31','2024-02-01','2024-02-02','2024-02-05','2024-02-06']
    panel=inputs().model_dump()
    panel['calendar']=dates
    panel['bars']=[bar(day) for day in dates]
    panel['signals']=[{'decision_date':day,'signal_version':'qa','candidates':[{'instrument_id':'000001.SZ','score':'1'}]} for day in dates]
    panel['evidence'].update(coverage_start=dates[0],coverage_end=dates[-1])
    result=simulate_portfolio(request(start=dates[0],end=dates[-1],rebalance=cadence),PortfolioInputs.model_validate(panel))
    assert result.status=='completed'
    assert [x['decision_date'] for x in result.targets]==expected


def test_artifact_recontains_all_rows_and_is_scope_specific(tmp_path):
    from app.services.stock_research_v2.portfolio import simulate_portfolio,save_portfolio_result
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store=ArtifactStore(tmp_path)
    result=simulate_portfolio(request(),inputs())
    artifact=save_portfolio_result(result,store)
    payload=store.read_json(artifact.sha256)
    assert payload['ledger']==result.model_dump(mode='json')['ledger']
    assert payload['request']['scope']['owner_id']==str(UUID(int=1))
    other=simulate_portfolio(request(scope={'owner_id':str(UUID(int=3)),'trading_mode':'virtual'}),inputs())
    assert save_portfolio_result(other,store).sha256 != artifact.sha256


def test_provenance_is_complete_and_input_order_does_not_change_semantic_result():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    panel=inputs()
    first=simulate_portfolio(request(),panel)
    reordered=panel.model_copy(update={'bars':list(reversed(panel.bars)), 'signals':[
        panel.signals[0].model_copy(update={'candidates':list(reversed(panel.signals[0].candidates))})]})
    second=simulate_portfolio(request(),reordered)
    assert len(first.input_sha256)==64 and first.input_sha256==second.input_sha256
    assert first.evidence==panel.evidence
    assert first.model_dump(mode='json')==second.model_dump(mode='json')
    altered=simulate_portfolio(request(),inputs(future_changes={'status':'suspended'}))
    assert altered.input_sha256 != first.input_sha256


def test_dates_do_not_guess_epochs():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        request(start=1704153600)


@pytest.mark.parametrize('bad', [True,'NaN','Infinity','-Infinity'])
def test_financial_decimal_contracts_reject_bool_and_nonfinite(bad):
    from app.schemas.stock_portfolio_v2 import Candidate,CostSettings,CostBreakdown,PositionLot,DailyAccount
    from pydantic import ValidationError
    constructors=[lambda:Candidate(instrument_id='000001.SZ',score=bad),
        lambda:CostSettings(commission_rate=bad), lambda:CostSettings(min_commission=bad),
        lambda:CostSettings(slippage_rate=bad),lambda:CostSettings(participation_rate=bad),
        lambda:CostBreakdown(commission=bad),lambda:CostBreakdown(total=bad),
        lambda:PositionLot(instrument_id='000001.SZ',quantity=100,cost_basis=bad,acquired_on='2024-01-02'),
        lambda:DailyAccount(trade_date='2024-01-02',cash=bad,position_value='0',receivables='0',
            equity='1',nav='1',daily_return='0',lots=[]),lambda:request(initial_cash=bad)]
    for construct in constructors:
        with pytest.raises(ValidationError):
            construct()


def test_all_date_contracts_reject_epochs_and_datetime_truncation():
    from app.schemas.stock_portfolio_v2 import (CorporateAction,SignalSnapshot,DailyAccount,ExecutionEvidence,PositionLot,PortfolioOrder)
    from datetime import datetime,timezone
    from pydantic import ValidationError
    for bad in [1704153600,datetime(2024,1,2,tzinfo=timezone.utc)]:
        constructors=[lambda:bar(day=bad),lambda:SignalSnapshot(decision_date=bad,signal_version='qa',candidates=[]),
            lambda:CorporateAction(event_id='qa',instrument_id='000001.SZ',kind='net_cash_dividend',record_date=bad,
                effective_date='2024-01-03',settlement_date='2024-01-04',cash_per_share='1',evidence_reference='qa'),
            lambda:DailyAccount(trade_date=bad,cash='1',position_value='0',receivables='0',equity='1',nav='1',daily_return='0',lots=[]),
            lambda:PositionLot(instrument_id='000001.SZ',quantity=100,cost_basis='1000',acquired_on=bad),
            lambda:PortfolioOrder(order_id='qa',instrument_id='000001.SZ',side='buy',quantity=100,
                decision_date=bad,execution_date='2024-01-03'),lambda:request(start=bad)]
        for construct in constructors:
            with pytest.raises(ValidationError):
                construct()
        from app.schemas.stock_portfolio_v2 import PortfolioInputs
        with pytest.raises(ValidationError):
            PortfolioInputs.model_validate({**inputs().model_dump(),'calendar':[bad,'2024-01-03']})
        with pytest.raises(ValidationError):
            ExecutionEvidence.model_validate({**inputs().evidence.model_dump(),'coverage_start':bad})


@pytest.mark.parametrize('bad', ['false',0,1,'true'])
def test_qa_only_is_strict_boolean(bad):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        request(qa_only=bad)


def test_unknown_fee_period_does_not_become_valid_when_allocation_is_zero():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    from app.schemas.stock_portfolio_v2 import PortfolioInputs
    panel=inputs().model_dump()
    dates=['2022-06-29','2022-06-30']
    panel.update(calendar=dates,bars=[],signals=[{'decision_date':dates[0],'signal_version':'qa','candidates':[]}])
    panel['evidence'].update(coverage_start=dates[0],coverage_end=dates[-1])
    result=simulate_portfolio(request(start=dates[0],end=dates[-1]),PortfolioInputs.model_validate(panel))
    assert result.status=='not_qualified' and result.daily==[] and 'fee_period_unverified' in result.blocking_reasons
