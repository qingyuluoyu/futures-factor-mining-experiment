"""Pure scoped portfolio ledger. No data-source certification, account writes or orders."""
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import re

from app.schemas.stock_portfolio_v2 import (DailyAccount, ExecutionResult, PortfolioInputs, PortfolioOrder,
    PortfolioRequest, PortfolioResult)
from app.services.stock_research_v2.artifacts import ArtifactStore, StoredArtifact
from app.services.stock_research_v2.corporate_actions import (apply_effective,
    capture_entitlement, settle_entitlement)
from app.services.stock_research_v2.costs import calculate_costs, money, FEE_REFERENCES
from app.services.stock_research_v2.execution import execute_order
from app.services.stock_research_v2.metrics import compute_metrics
from app.services.stock_research_v2.qualification import allows

LIMITATIONS = [
    'Calculation output does not certify source authorization, point-in-time history or execution evidence.',
    'Only 00xxxx.SZ/60xxxx.SH main-board shares with explicit daily limits; other boards rejected.',
    'D close signals size orders using D raw close; D+1 raw open matching, no future-status replacement.',
    'Daily volume is an ex-post participation upper bound; daily bars cannot prove opening-auction capacity or queue priority.',
    'Conservative rejection at adverse opening limit; no intraday retry, partial fill or fabricated path.',
    'Factor weights are direction-aware rank-linear weights, not score probabilities or leverage.',
    'Weekly/monthly rebalance uses the first supplied trading-calendar session in each period, plus the initial session.',
    'Net cash events require explicit taxes and record/ex/pay dates; unknown events and fractional shares stop replay.',
    'No final liquidation is assumed. Positions and unpaid entitlements remain in final equity.',
    'Commission includes exchange fees; transfer fee and sell stamp tax are separately rounded per fill.',
]


def _mainboard(instrument):
    return re.fullmatch(r'(?:60[0-9]{4}\.SH|00[0-9]{4}\.SZ)',instrument) is not None


def _weights(candidates, method):
    if not candidates:
        return []
    if method == 'mktcap':
        if any(x.marketcap is None for x in candidates):
            raise ValueError('missing_selected_marketcap')
        values=[x.marketcap for x in candidates]
    elif method == 'factor':
        values=[Decimal(len(candidates)-i) for i in range(len(candidates))]
    else:
        values=[Decimal(1)]*len(candidates)
    total=sum(values,Decimal('0'))
    return [value/total for value in values]


def _scheduled(calendar, start, end, rebalance, signals):
    dates=[x for x in calendar if start <= x < end]
    if rebalance == 'explicit':
        return {x for x in signals if start <= x < end}
    selected=set()
    previous=None
    for day in dates:
        period=day.isocalendar()[:2] if rebalance=='week' else (day.year,day.month)
        if period != previous:
            selected.add(day)
        previous=period
    return selected


def _input_digest(panels):
    """Stream framed canonical records rather than joining the full price grid in memory."""
    digest=hashlib.sha256()
    def feed(value):
        data=json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf8')
        digest.update(str(len(data)).encode('ascii')+b':'+data)
    feed({'input_version':'stock-portfolio-input-v1','calendar':[str(x) for x in panels.calendar],
        'evidence':panels.evidence.model_dump(mode='json')})
    for signal in sorted(panels.signals,key=lambda x:x.decision_date):
        item=signal.model_dump(mode='json')
        item['candidates']=sorted(item['candidates'],key=lambda x:x['instrument_id'])
        feed(item)
    for bar in sorted(panels.bars,key=lambda x:(x.trade_date,x.instrument_id)):
        feed(bar.model_dump(mode='json'))
    for event in sorted(panels.events,key=lambda x:x.event_id):
        feed(event.model_dump(mode='json'))
    return digest.hexdigest()


def simulate_portfolio(request: PortfolioRequest | dict, panels: PortfolioInputs | dict) -> PortfolioResult:
    # Revalidate even model_copy/update inputs; models are not a bypass of financial contracts.
    request=PortfolioRequest.model_validate(request.model_dump() if isinstance(request,PortfolioRequest) else request)
    panels=PortfolioInputs.model_validate(panels.model_dump() if isinstance(panels,PortfolioInputs) else panels)
    input_digest=_input_digest(panels)
    daily,targets,executions,ledger=[],[],[],[]
    blockers=[]
    def result(status,metrics=None):
        return PortfolioResult(request=request,validation_status='qa_only' if request.qa_only else 'execution_calculation_only',
            input_sha256=input_digest,evidence=panels.evidence,
            status=status,blocking_reasons=blockers,daily=daily,targets=targets,executions=executions,
            ledger=ledger,metrics=metrics,limitations=LIMITATIONS)
    if not request.qa_only and not allows(request.dataset.qualification, 'portfolio'):
        blockers.append('dataset_not_backtest_ready')
    try:
        calculate_costs(Decimal('1'),'buy',request.start,request.costs)
        calculate_costs(Decimal('1'),'buy',request.end,request.costs)
    except ValueError as error:
        blockers.append(str(error))
    for name in ('calendar_reference','raw_prices_reference','status_and_limits_reference',
            'corporate_actions_reference','delistings_reference','historical_universe_reference'):
        if getattr(panels.evidence,name) is None:
            blockers.append('missing_'+name)
    if panels.evidence.coverage_start>request.start or panels.evidence.coverage_end<request.end:
        blockers.append('evidence_coverage_gap')
    if request.start not in panels.calendar or request.end not in panels.calendar:
        blockers.append('request_calendar_boundary_missing')
    dates=[x for x in panels.calendar if request.start<=x<=request.end]
    bars={(x.trade_date,x.instrument_id):x for x in panels.bars}
    signals={x.decision_date:x for x in panels.signals}
    if any(day not in panels.calendar for day in signals):
        blockers.append('signal_outside_calendar')
    for event in panels.events:
        if event.record_date<request.start or any(day not in panels.calendar for day in (event.record_date,event.effective_date,event.settlement_date)):
            blockers.append('event_calendar_or_initial_entitlement_unavailable:'+event.event_id)
        if event.record_date>=event.effective_date:
            blockers.append('unsupported_event_timing:'+event.event_id)
    if blockers:
        return result('not_qualified')
    scheduled=_scheduled(panels.calendar,request.start,request.end,request.rebalance,signals)
    cash=request.initial_cash
    lots=[]
    entitlements={}
    events_effective,events_record,events_settle={},{},{}
    for event in panels.events:
        events_effective.setdefault(event.effective_date,[]).append(event)
        events_record.setdefault(event.record_date,[]).append(event)
        events_settle.setdefault(event.settlement_date,[]).append(event)
    pending=[]
    previous_equity=request.initial_cash
    previous_factors={}
    returns,navs,turnovers=[],[],[]
    for index,day in enumerate(dates):
        checkpoint=(len(targets),len(executions),len(ledger))
        try:
            # All applicable events are known data, but cannot influence D selection or sizing early.
            effective_events=events_effective.get(day,[])
            covered={x.instrument_id for x in effective_events if x.kind!='unsupported'}
            cash_delistings={x.instrument_id for x in effective_events if x.kind=='delisting_cash'}
            held={x.instrument_id for x in lots}
            for instrument in sorted(held):
                if instrument in cash_delistings:
                    continue
                bar=bars.get((day,instrument))
                if bar is None or bar.adj_factor is None:
                    raise ValueError('missing_holding_factor:'+instrument+':'+str(day))
                if instrument in previous_factors and bar.adj_factor!=previous_factors[instrument] and instrument not in covered:
                    raise ValueError('unexplained_factor_change:'+instrument+':'+str(day))
            for event in sorted(effective_events,key=lambda x:x.event_id):
                entitlement=entitlements.get(event.event_id)
                if entitlement is None:
                    raise ValueError('missing_event_entitlement:'+event.event_id)
                lots,line=apply_effective(entitlement,lots)
                ledger.append(line)
            for event in sorted(events_settle.get(day,[]),key=lambda x:x.event_id):
                entitlement=entitlements.get(event.event_id)
                if entitlement is None:
                    raise ValueError('missing_event_entitlement:'+event.event_id)
                lots,cash,line=settle_entitlement(entitlement,lots,cash)
                ledger.append(line)
            used={}
            traded=Decimal('0')
            # Decisions are fixed at previous close. Sells precede buys; tie order is stable instrument ID.
            for order in sorted(pending,key=lambda x:(x.side=='buy',x.instrument_id,x.order_id)):
                prior=bars.get((order.decision_date,order.instrument_id))
                current_bar=bars.get((day,order.instrument_id))
                if order.instrument_id in covered or (prior is not None and current_bar is not None and
                        prior.adj_factor is not None and current_bar.adj_factor is not None and prior.adj_factor!=current_bar.adj_factor):
                    matched=ExecutionResult(order=order,status='rejected',reason='corporate_action_between_decision_and_execution',cash=cash,lots=lots)
                else:
                    matched=execute_order(order,current_bar,lots,cash,request.costs,
                        used_volume=used.get(order.instrument_id,0))
                executions.append(matched)
                if matched.status=='filled':
                    cash,lots=matched.cash,matched.lots
                    traded+=matched.notional
                    used[order.instrument_id]=used.get(order.instrument_id,0)+order.quantity
                    common={'date':str(day),'order_id':order.order_id,'instrument_id':order.instrument_id}
                    ledger.extend([{**common,'kind':'cash','delta':str(matched.cash_delta),'balance':str(cash)},
                        {**common,'kind':'position','quantity_delta':order.quantity*(1 if order.side=='buy' else -1),
                            'realized_pnl':str(matched.realized_pnl)},
                        {**common,'kind':'fee',**matched.fees.model_dump(mode='json')}])
                else:
                    ledger.append({'kind':'rejection','date':str(day),'order_id':order.order_id,'reason':matched.reason})
            if pending:
                turnovers.append(float(traded/previous_equity))
            pending=[]
            position_value=Decimal('0')
            held={x.instrument_id for x in lots}
            pending_held={lot.instrument_id for item in entitlements.values() for lot in item.pending_lots}
            for instrument in sorted(held|pending_held):
                bar=bars.get((day,instrument))
                if bar is None or bar.close is None or bar.status is None or bar.adj_factor is None or bar.evidence_reference is None:
                    raise ValueError('missing_holding_valuation:'+instrument+':'+str(day))
                if bar.status=='delisted':
                    raise ValueError('unsettled_delisted_position:'+instrument+':'+str(day))
                position_value+=bar.close*sum(x.quantity for x in lots if x.instrument_id==instrument)
                previous_factors[instrument]=bar.adj_factor
            receivables=sum((x.cash_receivable for x in entitlements.values()),Decimal('0'))
            for item in entitlements.values():
                for lot in item.pending_lots:
                    receivables+=bars[(day,lot.instrument_id)].close*lot.quantity
            equity=money(cash+position_value+receivables)
            if equity<=0:
                raise ValueError('nonpositive_equity')
            # Record-date entitlement uses actual close holdings, including same-day buys.
            for event in sorted(events_record.get(day,[]),key=lambda x:x.event_id):
                entitlements[event.event_id]=capture_entitlement(event,lots)
            if day in scheduled:
                signal=signals.get(day)
                if signal is None:
                    raise ValueError('missing_scheduled_signal:'+str(day))
                selected=sorted(signal.candidates,key=lambda x:((-x.score if signal.direction=='high' else x.score),x.instrument_id))[:request.top_n]
                if any(not _mainboard(x.instrument_id) for x in selected):
                    raise ValueError('unsupported_selected_instrument')
                weights=_weights(selected,request.weight)
                quantities={}
                new_targets=[]
                next_day=dates[index+1]
                for candidate,weight in zip(selected,weights):
                    bar=bars.get((day,candidate.instrument_id))
                    if bar is None or bar.close is None or bar.adj_factor is None or bar.evidence_reference is None:
                        raise ValueError('missing_decision_raw_price:'+candidate.instrument_id+':'+str(day))
                    budget=equity*weight
                    sizing_price=min(money(bar.close*(1+request.costs.slippage_rate)),Decimal('1000000'))
                    quantity=int((budget/(sizing_price*100)).to_integral_value(rounding=ROUND_FLOOR))*100
                    while quantity>0:
                        notional=money(sizing_price*quantity)
                        if notional+calculate_costs(notional,'buy',next_day,request.costs).total<=budget:
                            break
                        quantity-=100
                    quantities[candidate.instrument_id]=quantity
                    new_targets.append({'decision_date':str(day),'execution_date':str(next_day),
                        'instrument_id':candidate.instrument_id,'score':str(candidate.score),'weight':str(weight),
                        'target_quantity':quantity,'decision_close':str(bar.close),'signal_version':signal.signal_version,
                        'factor_weight_policy':'directional_rank_linear' if request.weight=='factor' else None})
                targets.extend(new_targets)
                current={instrument:sum(x.quantity for x in lots if x.instrument_id==instrument) for instrument in held}
                for instrument in sorted(set(current)|set(quantities)):
                    difference=quantities.get(instrument,0)-current.get(instrument,0)
                    side='buy' if difference>0 else 'sell'
                    remaining=abs(difference)
                    part=0
                    while remaining:
                        size=min(remaining,1000000)
                        pending.append(PortfolioOrder(order_id=f'{day}:{instrument}:{side}:{part}',instrument_id=instrument,
                            side=side,quantity=size,decision_date=day,execution_date=next_day))
                        remaining-=size
                        part+=1
            daily_return=equity/previous_equity-1
            nav=equity/request.initial_cash
            daily.append(DailyAccount(trade_date=day,cash=cash,position_value=money(position_value),
                receivables=money(receivables),equity=equity,nav=nav,daily_return=daily_return,lots=lots))
            returns.append(float(daily_return));navs.append(float(nav))
            previous_equity=equity
        except ValueError as error:
            # Publish only whole closing-account days. No fill/event from an unvalued day leaks.
            del targets[checkpoint[0]:]
            del executions[checkpoint[1]:]
            del ledger[checkpoint[2]:]
            blockers.append(str(error))
            # No missing date is fabricated. Prefix metrics intentionally withheld on an incomplete range.
            return result('partially_completed' if daily else 'not_qualified')
    return result('completed',compute_metrics(returns,navs,turnovers,request.risk_free_annual))


def save_portfolio_result(result: PortfolioResult, store: ArtifactStore) -> StoredArtifact:
    """Full immutable JSON, never tail-truncated. Caller registers ownership via archive/DB."""
    result=PortfolioResult.model_validate(result.model_dump())
    payload=result.model_dump(mode='json')
    payload['fee_references']=list(FEE_REFERENCES)
    return store.put_json(payload)
