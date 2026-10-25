"""Conservative daily-open matching, no account mutation or intraday path inference."""
from datetime import date
from decimal import Decimal
import re

from app.schemas.stock_portfolio_v2 import CostSettings, ExecutionBar, ExecutionResult, PortfolioOrder, PositionLot
from app.services.stock_research_v2.costs import calculate_costs, money


def execute_order(order: PortfolioOrder, bar: ExecutionBar | None, lots: list[PositionLot], cash: Decimal,
        settings: CostSettings, *, used_volume: int = 0) -> ExecutionResult:
    order = PortfolioOrder.model_validate(order.model_dump() if isinstance(order,PortfolioOrder) else order)
    settings = CostSettings.model_validate(settings.model_dump() if isinstance(settings,CostSettings) else settings)
    lots = [PositionLot.model_validate(x.model_dump() if isinstance(x,PositionLot) else x) for x in lots]
    if not isinstance(cash, Decimal) or not cash.is_finite() or cash < 0 or type(used_volume) is not int or used_volume < 0:
        raise ValueError('invalid_account_state')

    def reject(reason):
        return ExecutionResult(order=order, status='rejected', reason=reason, cash=cash, lots=lots)

    if not re.fullmatch(r'(?:60[0-9]{4}\.SH|00[0-9]{4}\.SZ)', order.instrument_id):
        return reject('unsupported_instrument')
    if bar is None:
        return reject('missing_bar')
    bar = ExecutionBar.model_validate(bar.model_dump() if isinstance(bar,ExecutionBar) else bar)
    if bar.instrument_id != order.instrument_id or bar.trade_date != order.execution_date:
        raise ValueError('execution_bar_identity_mismatch')
    if bar.evidence_reference is None:
        return reject('missing_bar_evidence')
    if bar.status is None:
        return reject('missing_status')
    if bar.status != 'trading':
        return reject(bar.status)
    if any(x is None for x in (bar.open, bar.high, bar.low, bar.close)):
        return reject('missing_raw_price')
    if bar.adj_factor is None:
        return reject('missing_adj_factor')
    if bar.up_limit is None or bar.down_limit is None:
        return reject('missing_limit')
    if bar.volume_shares is None:
        return reject('missing_volume_or_unit')
    if order.side == 'buy' and order.quantity % 100:
        return reject('invalid_lot_size')
    total_position = sum(x.quantity for x in lots if x.instrument_id == order.instrument_id)
    if order.side == 'sell' and order.quantity % 100 and order.quantity % 100 != total_position % 100:
        return reject('invalid_odd_lot_sale')
    if order.side == 'buy' and bar.open >= bar.up_limit:
        return reject('limit_up_at_open')
    if order.side == 'sell' and bar.open <= bar.down_limit:
        return reject('limit_down_at_open')
    if order.quantity + used_volume > int(Decimal(bar.volume_shares) * settings.participation_rate):
        return reject('liquidity_limit')
    rate = Decimal('1') + settings.slippage_rate if order.side == 'buy' else Decimal('1') - settings.slippage_rate
    price = min(money(bar.open * rate), bar.up_limit) if order.side == 'buy' else max(money(bar.open * rate), bar.down_limit)
    notional = money(price * order.quantity)
    fees = calculate_costs(notional, order.side, bar.trade_date, settings)
    if order.side == 'buy':
        required = notional + fees.total
        if cash < required:
            return reject('insufficient_cash')
        updated = lots + [PositionLot(instrument_id=order.instrument_id, quantity=order.quantity,
            cost_basis=required, acquired_on=bar.trade_date)]
        return ExecutionResult(order=order, status='filled', price=price, notional=notional, fees=fees,
            cash=money(cash-required), cash_delta=-required, lots=updated)
    sellable = sum(x.quantity for x in lots if x.instrument_id == order.instrument_id and x.acquired_on < bar.trade_date)
    if sellable < order.quantity:
        return reject('t_plus_one_or_insufficient_position')
    proceeds = notional-fees.total
    if cash+proceeds < 0:
        return reject('insufficient_cash_for_fees')
    remaining, basis, updated = order.quantity, Decimal('0'), []
    for lot in sorted(lots, key=lambda x: (x.acquired_on, x.instrument_id)):
        if lot.instrument_id != order.instrument_id or lot.acquired_on >= bar.trade_date or remaining == 0:
            updated.append(lot)
            continue
        consumed = min(lot.quantity, remaining)
        removed_basis = lot.cost_basis if consumed == lot.quantity else money(lot.cost_basis * consumed / lot.quantity)
        basis += removed_basis
        if consumed < lot.quantity:
            updated.append(lot.model_copy(update={'quantity':lot.quantity-consumed, 'cost_basis':lot.cost_basis-removed_basis}))
        remaining -= consumed
    return ExecutionResult(order=order, status='filled', price=price, notional=notional, fees=fees,
        cash=money(cash+proceeds), cash_delta=proceeds, realized_pnl=money(proceeds-basis), lots=updated)
