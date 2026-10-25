"""Explicit net events only. Unknown taxes, fractional entitlements and rights stop replay."""
from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.stock_portfolio_v2 import CorporateAction, PositionLot
from app.services.stock_research_v2.costs import money

METHOD_VERSION = 'stock-explicit-corporate-events-2026-10-25.v1'


@dataclass
class Entitlement:
    event: CorporateAction
    quantity: int
    effective: bool = False
    settled: bool = False
    cash_receivable: Decimal = Decimal('0')
    pending_lots: list[PositionLot] = field(default_factory=list)


def capture_entitlement(event: CorporateAction, lots: list[PositionLot]) -> Entitlement:
    return Entitlement(event, sum(x.quantity for x in lots if x.instrument_id == event.instrument_id))


def apply_effective(entitlement: Entitlement, lots: list[PositionLot]) -> tuple[list[PositionLot], dict]:
    event, quantity = entitlement.event, entitlement.quantity
    if entitlement.effective:
        raise ValueError('duplicate_event_application')
    if event.kind == 'unsupported':
        raise ValueError('unsupported_corporate_action:'+event.event_id)
    current = sum(x.quantity for x in lots if x.instrument_id == event.instrument_id)
    updated = list(lots)
    if event.kind == 'net_cash_dividend':
        entitlement.cash_receivable = money(Decimal(quantity)*event.cash_per_share)
    elif event.kind == 'delisting_cash':
        if current != quantity:
            raise ValueError('unsupported_entitlement_position_change:'+event.event_id)
        entitlement.cash_receivable = money(Decimal(quantity)*event.cash_per_share)
        updated = [x for x in lots if x.instrument_id != event.instrument_id]
    elif event.kind == 'share_distribution':
        if event.share_multiplier < 1 or current != quantity:
            raise ValueError('unsupported_share_distribution:'+event.event_id)
        updated=[]
        for lot in lots:
            if lot.instrument_id != event.instrument_id:
                updated.append(lot)
                continue
            extra = Decimal(lot.quantity)*(event.share_multiplier-1)
            if extra != extra.to_integral_value():
                raise ValueError('fractional_share_entitlement:'+event.event_id)
            retained_basis = money(lot.cost_basis/event.share_multiplier)
            updated.append(lot.model_copy(update={'cost_basis':retained_basis}))
            if extra:
                entitlement.pending_lots.append(PositionLot(instrument_id=lot.instrument_id,
                    quantity=int(extra), cost_basis=lot.cost_basis-retained_basis, acquired_on=lot.acquired_on))
    entitlement.effective=True
    return updated, {'kind':'corporate_action','stage':'effective','event_id':event.event_id,
        'instrument_id':event.instrument_id,'date':event.effective_date.isoformat(),'quantity':quantity,
        'cash_receivable':str(entitlement.cash_receivable),'pending_shares':sum(x.quantity for x in entitlement.pending_lots),
        'event_kind':event.kind,'evidence_reference':event.evidence_reference,'method_version':METHOD_VERSION}


def settle_entitlement(entitlement: Entitlement, lots: list[PositionLot], cash: Decimal) -> tuple[list[PositionLot], Decimal, dict]:
    if not entitlement.effective or entitlement.settled:
        raise ValueError('invalid_event_settlement')
    event=entitlement.event
    delta=entitlement.cash_receivable
    updated=list(lots)+entitlement.pending_lots
    entitlement.cash_receivable=Decimal('0')
    entitlement.pending_lots=[]
    entitlement.settled=True
    return updated,money(cash+delta),{'kind':'corporate_action','stage':'settled','event_id':event.event_id,
        'date':event.settlement_date.isoformat(),'instrument_id':event.instrument_id,'cash_delta':str(delta),
        'event_kind':event.kind,'evidence_reference':event.evidence_reference,'method_version':METHOD_VERSION}
