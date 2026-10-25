"""Bounded historical fee schedule; commissioned broker rate includes exchange fees."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.schemas.stock_portfolio_v2 import CostBreakdown, CostSettings

METHOD_VERSION = 'stock-mainboard-costs-2026-10-25.v1'
FEE_REFERENCES = (
    'https://one.sse.com.cn/onething/gptz/',
    'https://tianjin.chinatax.gov.cn/11200000000/0300/030005/20220725150235434.shtml',
    'https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20230827_5725662.shtml',
)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def calculate_costs(notional: Decimal, side: str, day: date, settings: CostSettings) -> CostBreakdown:
    settings = CostSettings.model_validate(settings.model_dump() if isinstance(settings,CostSettings) else settings)
    if not date(2022, 7, 1) <= day <= date(2026, 10, 25):
        raise ValueError('fee_period_unverified')
    if not isinstance(notional, Decimal) or not notional.is_finite() or notional <= 0 or side not in ('buy', 'sell'):
        raise ValueError('invalid_fee_input')
    commission = money(max(settings.min_commission, notional * settings.commission_rate))
    transfer_fee = money(notional * Decimal('.00001'))
    stamp_rate = Decimal('.0005') if day >= date(2023, 8, 28) else Decimal('.001')
    stamp_tax = money(notional * stamp_rate) if side == 'sell' else Decimal('0')
    return CostBreakdown(commission=commission, transfer_fee=transfer_fee, stamp_tax=stamp_tax,
        total=commission + transfer_fee + stamp_tax)
