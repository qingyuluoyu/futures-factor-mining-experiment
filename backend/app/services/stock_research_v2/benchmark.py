"""Non-tradable index comparison and an actual R3 equal-weight buy-and-hold baseline."""
from decimal import Decimal
import hashlib
import json
import math
from statistics import mean,stdev

from app.schemas.stock_benchmark_v2 import BenchmarkComparison,IndexComparisonDay,IndexSeries
from app.schemas.stock_portfolio_v2 import PortfolioInputs,PortfolioRequest,PortfolioResult,SignalSnapshot
from app.services.stock_research_v2.metrics import compute_metrics,METHOD_VERSION,PERIODS_PER_YEAR
from app.services.stock_research_v2.portfolio import simulate_portfolio


class BenchmarkError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def _digest(model):
    digest=hashlib.sha256()
    payload=model.model_dump(mode='json')
    for chunk in json.JSONEncoder(sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).iterencode(payload):
        digest.update(chunk.encode('utf8'))
    return digest.hexdigest()


def _validated_portfolio(value):
    value=PortfolioResult.model_validate(value.model_dump() if isinstance(value,PortfolioResult) else value)
    if value.status!='completed' or value.blocking_reasons or value.metrics is None:
        raise BenchmarkError('incomplete_portfolio')
    if value.metrics.get('method_version')!=METHOD_VERSION:
        raise BenchmarkError('incompatible_metric_method')
    dates=[x.trade_date for x in value.daily]
    if len(dates)<2 or dates!=sorted(set(dates)) or dates[0]!=value.request.start or dates[-1]!=value.request.end:
        raise BenchmarkError('invalid_portfolio_calendar')
    previous=value.request.initial_cash
    for row in value.daily:
        if row.cash<0 or row.position_value<0 or row.receivables<0 or row.equity<=0 or row.equity!=row.cash+row.position_value+row.receivables:
            raise BenchmarkError('portfolio_accounting_mismatch')
        if row.nav!=row.equity/value.request.initial_cash or row.daily_return!=row.equity/previous-1:
            raise BenchmarkError('portfolio_nav_mismatch')
        previous=row.equity
    return value


def _recomputed_metrics(value):
    previous={row.trade_date:value.daily[index-1].equity for index,row in enumerate(value.daily) if index>0}
    traded={}
    for execution in value.executions:
        day=execution.order.execution_date
        if day not in previous:
            raise BenchmarkError('execution_outside_comparison_calendar')
        traded.setdefault(day,Decimal('0'))
        if execution.status=='filled':traded[day]+=execution.notional
    turnover=[float(traded[day]/previous[day]) for day in sorted(traded)]
    return compute_metrics([float(x.daily_return) for x in value.daily],[float(x.nav) for x in value.daily],
        turnover,value.request.risk_free_annual)


def _active_metrics(left_returns,right_returns):
    active=[float(left-right) for left,right in zip(left_returns,right_returns)]
    deviation=stdev(active) if len(active)>1 else None
    tracking=deviation*math.sqrt(PERIODS_PER_YEAR) if deviation is not None else None
    ratio=mean(active)/deviation*math.sqrt(PERIODS_PER_YEAR) if deviation else None
    return {'periods_per_year':PERIODS_PER_YEAR,'std_ddof':1,'tracking_error':tracking,'information_ratio':ratio,
        'active_return_basis':'arithmetic_daily_return_difference',
        'undefined_reasons':{'information_ratio':'zero_active_volatility' if deviation==0 else 'insufficient_returns'} if not deviation else {}}


def compare_to_index(strategy:PortfolioResult|dict,index:IndexSeries|dict)->BenchmarkComparison:
    strategy=_validated_portfolio(strategy)
    index=IndexSeries.model_validate(index.model_dump() if isinstance(index,IndexSeries) else index)
    if index.spec.calendar_reference!=strategy.evidence.calendar_reference:
        raise BenchmarkError('calendar_reference_mismatch')
    if [x.trade_date for x in index.rows]!=[x.trade_date for x in strategy.daily]:
        raise BenchmarkError('calendar_mismatch')
    if strategy.daily[0].nav!=1 or strategy.daily[0].daily_return!=0:
        raise BenchmarkError('incompatible_initial_execution_window')
    if index.rows[1].open is None:
        raise BenchmarkError('missing_execution_anchor')
    if any(x.close is None for x in index.rows):
        raise BenchmarkError('missing_index_close')
    anchor=index.rows[1].open
    benchmark_navs=[Decimal('1')]+[x.close/anchor for x in index.rows[1:]]
    benchmark_returns=[]
    previous=Decimal('1')
    for nav in benchmark_navs:
        benchmark_returns.append(nav/previous-1)
        previous=nav
    daily=[IndexComparisonDay(trade_date=row.trade_date,strategy_nav=row.nav,benchmark_nav=nav,
        strategy_return=row.daily_return,benchmark_return=change,return_difference=row.nav-nav,
        relative_wealth_return=row.nav/nav-1) for row,nav,change in zip(strategy.daily,benchmark_navs,benchmark_returns)]
    warnings=['index_is_nontradable_and_costs_differ','evidence_references_do_not_certify_sources',
        'return_difference_is_descriptive_not_investable_excess','first_day_cash_then_next_session_open_anchor']
    if index.spec.kind=='price_index':warnings.append('price_index_excludes_distributions')
    else:warnings.append('index_dividend_reinvestment_and_strategy_cash_events_have_distinct_assumptions')
    return BenchmarkComparison(validation_status='qa_only' if strategy.request.qa_only else 'calculation_only',
        source_qualification=index.spec.qualification,strategy_parameters=strategy.request,
        strategy_input_sha256=strategy.input_sha256,strategy_content_sha256=_digest(strategy),spec=index.spec,
        index_content_sha256=_digest(index),index_rows=index.rows,daily=daily,
        strategy_metrics=_recomputed_metrics(strategy),
        benchmark_metrics=compute_metrics([float(x) for x in benchmark_returns],[float(x) for x in benchmark_navs],[],strategy.request.risk_free_annual),
        active_metrics=_active_metrics([x.daily_return for x in strategy.daily],benchmark_returns),warnings=warnings)


def run_equal_weight_buy_hold(request:PortfolioRequest|dict,panels:PortfolioInputs|dict,
        initial_signal:SignalSnapshot|dict|None=None)->PortfolioResult:
    request=PortfolioRequest.model_validate(request.model_dump() if isinstance(request,PortfolioRequest) else request)
    panels=PortfolioInputs.model_validate(panels.model_dump() if isinstance(panels,PortfolioInputs) else panels)
    if initial_signal is None:
        initial_signal=next((x for x in panels.signals if x.decision_date==request.start),None)
    if initial_signal is None:raise BenchmarkError('missing_initial_pool')
    signal=SignalSnapshot.model_validate(initial_signal.model_dump() if isinstance(initial_signal,SignalSnapshot) else initial_signal)
    if signal.decision_date!=request.start:raise BenchmarkError('initial_pool_date_mismatch')
    if not 1<=len(signal.candidates)<=300:raise BenchmarkError('initial_pool_size_outside_execution_budget')
    initial_pool=[{'instrument_id':x.instrument_id,'score':'0'} for x in sorted(signal.candidates,key=lambda x:x.instrument_id)]
    version=hashlib.sha256(json.dumps({'initial_pool':initial_pool,'source_version':signal.signal_version},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    baseline=SignalSnapshot(decision_date=request.start,candidates=initial_pool,direction='high',
        signal_version='equal-weight-buy-hold:'+version)
    request_payload=request.model_dump()
    request_payload.update(weight='equal',rebalance='explicit',top_n=len(initial_pool))
    panels_payload=panels.model_dump()
    panels_payload['signals']=[baseline.model_dump()]
    return simulate_portfolio(PortfolioRequest.model_validate(request_payload),PortfolioInputs.model_validate(panels_payload))
