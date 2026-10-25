from decimal import Decimal as D

import pytest

from tests.test_stock_portfolio_v2 import request, inputs


def portfolio():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    return simulate_portfolio(request(),inputs())


def index_series(**spec_changes):
    from app.schemas.stock_benchmark_v2 import IndexSeries
    spec={'instrument_id':'000300.SH','kind':'price_index','currency':'CNY','market':'CN_A',
        'timezone':'Asia/Shanghai','frequency':'1d','level_unit':'point','provider':'qa',
        'source_reference':'qa:index-source','authorization_reference':'qa:permission',
        'methodology_reference':'qa:index-method','calendar_reference':'qa:complete-coverage',
        'data_version':'b'*64,'qualification':'unverified','total_return_basis':'not_applicable',
        'anchor':'next_session_open',**spec_changes}
    return IndexSeries.model_validate({'spec':spec,'rows':[
        {'trade_date':'2024-01-02','open':'99','close':'100'},
        {'trade_date':'2024-01-03','open':'110','close':'121'},
        {'trade_date':'2024-01-04','open':'121','close':'110'},
        {'trade_date':'2024-01-05','open':'110','close':'115.5'}]})


def test_index_uses_next_session_open_and_aligned_244_metrics_with_basis_labels():
    from app.services.stock_research_v2.benchmark import compare_to_index
    result=compare_to_index(portfolio(),index_series())
    assert result.validation_status=='qa_only'
    assert [x.benchmark_nav for x in result.daily]==[D('1'),D('1.1'),D('1'),D('1.05')]
    assert result.benchmark_metrics['periods_per_year']==244
    assert result.benchmark_metrics['risk_free_annual']==0
    assert result.benchmark_metrics['total_return']==pytest.approx(.05)
    assert result.daily[-1].return_difference==D('-.050509')
    assert not result.economic_excess_available
    assert 'price_index_excludes_distributions' in result.warnings
    assert result.source_qualification=='unverified'


@pytest.mark.parametrize('change,reason', [('missing_day','calendar_mismatch'),('no_anchor','missing_execution_anchor'),
    ('no_close','missing_index_close'),('different_calendar','calendar_reference_mismatch')])
def test_alignment_or_unknown_values_are_not_filled(change,reason):
    from app.services.stock_research_v2.benchmark import compare_to_index,BenchmarkError
    series=index_series()
    payload=series.model_dump()
    if change=='missing_day': payload['rows'].pop(2)
    if change=='no_anchor': payload['rows'][1]['open']=None
    if change=='no_close': payload['rows'][2]['close']=None
    if change=='different_calendar': payload['spec']['calendar_reference']='other'
    with pytest.raises(BenchmarkError,match=reason):
        compare_to_index(portfolio(),payload)


@pytest.mark.parametrize('bad', [True,'NaN','Infinity','0','-1'])
def test_illegal_index_level_fails_schema(bad):
    from app.schemas.stock_benchmark_v2 import IndexSeries
    from pydantic import ValidationError
    payload=index_series().model_dump()
    payload['rows'][1]['close']=bad
    with pytest.raises(ValidationError): IndexSeries.model_validate(payload)


def test_total_return_index_requires_explicit_reinvestment_basis_and_no_auto_certification():
    from app.schemas.stock_benchmark_v2 import BenchmarkSpec
    from app.services.stock_research_v2.benchmark import compare_to_index
    from pydantic import ValidationError
    with pytest.raises(ValidationError): index_series(kind='total_return_index')
    result=compare_to_index(portfolio(),index_series(kind='total_return_index',total_return_basis='gross_reinvested',qualification='verified'))
    assert result.validation_status=='qa_only' and not result.economic_excess_available
    assert result.spec.total_return_basis=='gross_reinvested'
    with pytest.raises(ValidationError):
        BenchmarkSpec.model_validate({**index_series().spec.model_dump(),'instrument_id':'600000.SH'})


def test_equal_weight_buy_hold_reuses_real_fills_and_holds_original_pool():
    from app.services.stock_research_v2.benchmark import run_equal_weight_buy_hold
    panel=inputs()
    result=run_equal_weight_buy_hold(request(),panel,panel.signals[0])
    assert result.status=='completed' and result.request.weight=='equal' and result.request.top_n==2
    assert len(result.targets)==2 and all(D(x['weight'])==D('.5') for x in result.targets)
    assert len(result.executions)==2 and all(x.status=='filled' and x.fees.total>0 for x in result.executions)
    assert all(str(x.order.decision_date)=='2024-01-02' for x in result.executions)
    assert result.daily[-1].equity==D('9989.92')


def test_blank_references_and_epoch_dates_are_not_evidence():
    from app.schemas.stock_benchmark_v2 import IndexSeries
    from pydantic import ValidationError
    for name in ['source_reference','authorization_reference','methodology_reference','calendar_reference','provider']:
        with pytest.raises(ValidationError):index_series(**{name:'   '})
    for bad in [1704153600,True]:
        payload=index_series().model_dump()
        payload['rows'][0]['trade_date']=bad
        with pytest.raises(ValidationError):IndexSeries.model_validate(payload)


def test_index_duplicates_calendar_gaps_and_missing_metadata_are_rejected():
    from app.schemas.stock_benchmark_v2 import IndexSeries
    from pydantic import ValidationError
    payload=index_series().model_dump()
    payload['rows'].insert(1,payload['rows'][0])
    with pytest.raises(ValidationError):IndexSeries.model_validate(payload)
    for name in ['authorization_reference','data_version','total_return_basis','currency']:
        payload=index_series().model_dump()
        del payload['spec'][name]
        with pytest.raises(ValidationError):IndexSeries.model_validate(payload)


def test_buy_hold_ignores_future_rankings_without_removing_future_suspended_initial_member():
    from app.services.stock_research_v2.benchmark import run_equal_weight_buy_hold
    panel=inputs(future_changes={'status':'suspended'})
    result=run_equal_weight_buy_hold(request(),panel)
    assert {x['instrument_id'] for x in result.targets}=={'000001.SZ','600000.SH'}
    assert [x.reason for x in result.executions if x.status=='rejected']==['suspended']
    assert result.request.top_n==2
