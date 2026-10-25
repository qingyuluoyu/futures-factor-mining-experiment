"""Strict same-experiment strategy comparisons and immutable full report saving."""
from app.schemas.stock_benchmark_v2 import BenchmarkComparison,StrategyComparison,StrategyComparisonDay
from app.services.stock_research_v2.artifacts import ArtifactStore,StoredArtifact
from app.services.stock_research_v2.benchmark import BenchmarkError,_active_metrics,_digest,_recomputed_metrics,_validated_portfolio


def compare_strategies(left,right)->StrategyComparison:
    left,right=_validated_portfolio(left),_validated_portfolio(right)
    for field,code in [('scope','scope_mismatch'),('dataset','dataset_mismatch'),('pool_version','pool_version_mismatch'),
            ('costs','cost_settings_mismatch'),('cost_version','cost_version_mismatch'),
            ('execution_version','execution_version_mismatch'),('risk_free_annual','risk_free_mismatch')]:
        if getattr(left.request,field)!=getattr(right.request,field):raise BenchmarkError(code)
    if left.request.start!=right.request.start or left.request.end!=right.request.end:
        raise BenchmarkError('date_range_mismatch')
    if left.request.initial_cash!=right.request.initial_cash:
        raise BenchmarkError('initial_cash_mismatch')
    if left.evidence!=right.evidence:raise BenchmarkError('evidence_mismatch')
    if left.request.qa_only!=right.request.qa_only or left.validation_status!=right.validation_status:
        raise BenchmarkError('validation_scope_mismatch')
    if [x.trade_date for x in left.daily]!=[x.trade_date for x in right.daily]:raise BenchmarkError('calendar_mismatch')
    daily=[StrategyComparisonDay(trade_date=a.trade_date,left_nav=a.nav,right_nav=b.nav,left_return=a.daily_return,
        right_return=b.daily_return,return_difference=a.nav-b.nav,relative_wealth_return=a.nav/b.nav-1)
        for a,b in zip(left.daily,right.daily)]
    return StrategyComparison(validation_status='qa_only' if left.request.qa_only else 'calculation_only',
        left_parameters=left.request,right_parameters=right.request,left_input_sha256=left.input_sha256,
        right_input_sha256=right.input_sha256,left_content_sha256=_digest(left),right_content_sha256=_digest(right),
        daily=daily,left_metrics=_recomputed_metrics(left),right_metrics=_recomputed_metrics(right),
        active_metrics=_active_metrics([x.daily_return for x in left.daily],[x.daily_return for x in right.daily]),
        warnings=['comparison_does_not_certify_source_or_strategy','same_scope_data_calendar_cost_and_metric_method_required',
            'different_signal_and_portfolio_parameters_are_preserved'])


def save_comparison(report:BenchmarkComparison|StrategyComparison,store:ArtifactStore)->StoredArtifact:
    if isinstance(report,BenchmarkComparison):report=BenchmarkComparison.model_validate(report.model_dump())
    elif isinstance(report,StrategyComparison):report=StrategyComparison.model_validate(report.model_dump())
    else:raise BenchmarkError('unsupported_comparison_report')
    return store.put_json(report.model_dump(mode='json'))
