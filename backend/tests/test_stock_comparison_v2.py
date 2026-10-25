from decimal import Decimal as D
from uuid import UUID

import pytest

from tests.test_stock_portfolio_v2 import request,inputs
from tests.test_stock_benchmark_v2 import index_series,portfolio


def test_two_strategies_compare_full_same_scope_data_dates_and_costs():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    from app.services.stock_research_v2.comparison import compare_strategies
    left=portfolio()
    right=simulate_portfolio(request(top_n=2),inputs())
    result=compare_strategies(left,right)
    assert result.daily[-1].left_nav-result.daily[-1].right_nav==D('.000499')
    assert result.daily[-1].return_difference==D('.000499')
    assert result.left_parameters.top_n==1 and result.right_parameters.top_n==2
    assert result.left_metrics['periods_per_year']==result.right_metrics['periods_per_year']==244


@pytest.mark.parametrize('change,reason', [('scope','scope_mismatch'),('dataset','dataset_mismatch'),
    ('pool','pool_version_mismatch'),('costs','cost_settings_mismatch'),('risk_free','risk_free_mismatch'),
    ('partial','incomplete_portfolio')])
def test_incompatible_strategies_fail_before_showing_differences(change,reason):
    from app.services.stock_research_v2.comparison import compare_strategies
    from app.services.stock_research_v2.benchmark import BenchmarkError
    left=portfolio()
    right=left.model_dump()
    if change=='scope':right['request']['scope']['owner_id']=UUID(int=3)
    if change=='dataset':right['request']['dataset']['data_version']='c'*64
    if change=='pool':right['request']['pool_version']='different'
    if change=='costs':right['request']['costs']['slippage_rate']=D('.002')
    if change=='risk_free':right['request']['risk_free_annual']=.05
    if change=='partial':right['status']='partially_completed'
    with pytest.raises(BenchmarkError,match=reason):compare_strategies(left,right)


def test_report_saves_full_parameters_and_comparison_deterministically(tmp_path):
    from app.services.stock_research_v2.benchmark import compare_to_index
    from app.services.stock_research_v2.comparison import save_comparison
    from app.services.stock_research_v2.artifacts import ArtifactStore
    result=compare_to_index(portfolio(),index_series())
    store=ArtifactStore(tmp_path)
    saved=save_comparison(result,store)
    assert saved==save_comparison(result,store)
    payload=store.read_json(saved.sha256)
    assert payload['strategy_parameters']['scope']['owner_id']==str(UUID(int=1))
    assert payload['spec']['data_version']=='b'*64
    assert len(payload['daily'])==4


def test_initial_cash_and_accounting_or_method_tampering_are_not_silently_compared():
    from app.services.stock_research_v2.portfolio import simulate_portfolio
    from app.services.stock_research_v2.comparison import compare_strategies
    from app.services.stock_research_v2.benchmark import BenchmarkError,compare_to_index
    left=portfolio()
    other=simulate_portfolio(request(initial_cash='20000'),inputs())
    with pytest.raises(BenchmarkError,match='initial_cash_mismatch'):compare_strategies(left,other)
    bad=left.model_dump()
    bad['daily'][1]['cash']+=D('1')
    with pytest.raises(BenchmarkError,match='portfolio_accounting_mismatch'):compare_to_index(bad,index_series())
    bad=left.model_dump()
    bad['metrics']['method_version']='other'
    with pytest.raises(BenchmarkError,match='incompatible_metric_method'):compare_strategies(left,bad)


def test_identical_strategy_has_zero_tracking_error_and_null_information_ratio():
    from app.services.stock_research_v2.comparison import compare_strategies
    result=compare_strategies(portfolio(),portfolio())
    assert result.active_metrics['tracking_error']==0
    assert result.active_metrics['information_ratio'] is None
    assert result.active_metrics['undefined_reasons']['information_ratio']=='zero_active_volatility'
    assert all(x.return_difference==0 for x in result.daily)
