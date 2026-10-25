"""Versioned, pure main-board simulation contracts; never an account/trading API."""
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, DatasetRef, Scope, equity_identifier, explicit_date

EXECUTION_VERSION = 'stock-mainboard-daily-execution-2026-10-25.v2'
COST_VERSION = 'stock-mainboard-costs-2026-10-25.v1'


class CostSettings(Contract):
    commission_rate: Decimal = Field(default=Decimal('.0003'), ge=0, le=Decimal('.003'))
    min_commission: Decimal = Field(default=Decimal('5'), ge=0, le=100, decimal_places=2)
    slippage_rate: Decimal = Field(default=Decimal('.001'), ge=0, le=Decimal('.02'))
    participation_rate: Decimal = Field(default=Decimal('.1'), gt=0, le=Decimal('.1'))
    commission_basis: Literal['inclusive_exchange_fees'] = 'inclusive_exchange_fees'


class CostBreakdown(Contract):
    commission: Decimal = Decimal('0')
    transfer_fee: Decimal = Decimal('0')
    stamp_tax: Decimal = Decimal('0')
    total: Decimal = Decimal('0')
    cost_version: Literal[COST_VERSION] = COST_VERSION


class ExecutionBar(Contract):
    instrument_id: str
    trade_date: date
    open: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    high: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    low: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    close: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    volume: Decimal | None = Field(default=None, ge=0, le=Decimal('1e14'))
    volume_unit: Literal['share', 'lot_100_shares'] | None = None
    adj_factor: Decimal | None = Field(default=None, gt=0, le=Decimal('1e14'))
    status: Literal['trading', 'suspended', 'delisted'] | None = None
    up_limit: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    down_limit: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)
    price_basis: Literal['raw'] = 'raw'
    evidence_reference: str | None = Field(default=None, min_length=1, max_length=200)

    _identifier = field_validator('instrument_id')(equity_identifier)
    _date = field_validator('trade_date', mode='before')(explicit_date)

    @property
    def volume_shares(self) -> int | None:
        if self.volume is None or self.volume_unit is None:
            return None
        number = self.volume * (100 if self.volume_unit == 'lot_100_shares' else 1)
        if number != number.to_integral_value():
            raise ValueError('volume must convert to integral shares')
        return int(number)

    @model_validator(mode='after')
    def consistency(self):
        self.volume_shares
        if all(value is not None for value in (self.open, self.high, self.low, self.close)):
            if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
                raise ValueError('inconsistent raw OHLC')
        if self.up_limit is not None and self.down_limit is not None:
            if self.up_limit <= self.down_limit:
                raise ValueError('inconsistent official limits')
            for price in (self.open, self.high, self.low, self.close):
                if price is not None and not self.down_limit <= price <= self.up_limit:
                    raise ValueError('raw price outside official limits')
        return self


class PositionLot(Contract):
    instrument_id: str
    quantity: int = Field(strict=True, gt=0, le=10000000000)
    cost_basis: Decimal = Field(ge=0)
    acquired_on: date
    _identifier = field_validator('instrument_id')(equity_identifier)
    _date = field_validator('acquired_on', mode='before')(explicit_date)


class PortfolioOrder(Contract):
    order_id: str = Field(min_length=1, max_length=160)
    instrument_id: str
    side: Literal['buy', 'sell']
    quantity: int = Field(strict=True, gt=0, le=1000000)
    decision_date: date
    execution_date: date
    _identifier = field_validator('instrument_id')(equity_identifier)
    _date = field_validator('decision_date', 'execution_date', mode='before')(explicit_date)

    @model_validator(mode='after')
    def next_day(self):
        if self.execution_date <= self.decision_date:
            raise ValueError('execution_date must follow decision_date')
        return self


class ExecutionResult(Contract):
    order: PortfolioOrder
    status: Literal['filled', 'rejected']
    reason: str | None = None
    price: Decimal | None = None
    notional: Decimal = Decimal('0')
    fees: CostBreakdown = Field(default_factory=CostBreakdown)
    cash: Decimal
    cash_delta: Decimal = Decimal('0')
    realized_pnl: Decimal = Decimal('0')
    lots: list[PositionLot]


class Candidate(Contract):
    instrument_id: str
    score: Decimal = Field(ge=Decimal('-1e30'), le=Decimal('1e30'))
    marketcap: Decimal | None = Field(default=None, gt=0, le=Decimal('1e30'))
    _identifier = field_validator('instrument_id')(equity_identifier)


class SignalSnapshot(Contract):
    decision_date: date
    candidates: list[Candidate] = Field(max_length=5000)
    signal_version: str = Field(min_length=1, max_length=200)
    direction: Literal['high', 'low'] = 'high'
    _date = field_validator('decision_date', mode='before')(explicit_date)

    @model_validator(mode='after')
    def unique(self):
        if len({x.instrument_id for x in self.candidates}) != len(self.candidates):
            raise ValueError('duplicate signal instrument')
        return self


class ExecutionEvidence(Contract):
    """References supplied by a trusted adapter; these fields do not certify a source."""
    calendar_reference: str | None = Field(default=None, min_length=1, max_length=200)
    raw_prices_reference: str | None = Field(default=None, min_length=1, max_length=200)
    status_and_limits_reference: str | None = Field(default=None, min_length=1, max_length=200)
    corporate_actions_reference: str | None = Field(default=None, min_length=1, max_length=200)
    delistings_reference: str | None = Field(default=None, min_length=1, max_length=200)
    historical_universe_reference: str | None = Field(default=None, min_length=1, max_length=200)
    coverage_start: date
    coverage_end: date
    _date = field_validator('coverage_start', 'coverage_end', mode='before')(explicit_date)

    @model_validator(mode='after')
    def interval(self):
        if self.coverage_start > self.coverage_end:
            raise ValueError('invalid evidence coverage')
        return self


class CorporateAction(Contract):
    event_id: str = Field(min_length=1, max_length=160)
    instrument_id: str
    kind: Literal['net_cash_dividend', 'share_distribution', 'delisting_cash', 'unsupported']
    record_date: date
    effective_date: date
    settlement_date: date
    cash_per_share: Decimal | None = Field(default=None, ge=0)
    share_multiplier: Decimal | None = Field(default=None, gt=0)
    evidence_reference: str = Field(min_length=1, max_length=200)
    _identifier = field_validator('instrument_id')(equity_identifier)
    _date = field_validator('record_date', 'effective_date', 'settlement_date', mode='before')(explicit_date)

    @model_validator(mode='after')
    def event_contract(self):
        if not self.record_date <= self.effective_date <= self.settlement_date:
            raise ValueError('record/effective/settlement dates inconsistent')
        if self.kind in ('net_cash_dividend', 'delisting_cash') and (self.cash_per_share is None or self.share_multiplier is not None):
            raise ValueError('cash event requires explicit net cash_per_share only')
        if self.kind == 'share_distribution' and (self.share_multiplier is None or self.cash_per_share is not None):
            raise ValueError('share event requires explicit multiplier only')
        return self


class PortfolioInputs(Contract):
    calendar: list[date] = Field(min_length=2, max_length=20000)
    signals: list[SignalSnapshot] = Field(min_length=1, max_length=20000)
    bars: list[ExecutionBar] = Field(max_length=2000000)
    evidence: ExecutionEvidence
    events: list[CorporateAction] = Field(default_factory=list, max_length=20000)

    @field_validator('calendar',mode='before')
    @classmethod
    def explicit_calendar_dates(cls, values):
        if not isinstance(values,list):
            raise ValueError('calendar must be an explicit list')
        return [explicit_date(value) for value in values]

    @model_validator(mode='after')
    def unique_axes(self):
        if self.calendar != sorted(set(self.calendar)):
            raise ValueError('calendar must be explicit, unique and ascending')
        if len({(x.trade_date,x.instrument_id) for x in self.bars}) != len(self.bars):
            raise ValueError('duplicate execution bar')
        if len({x.decision_date for x in self.signals}) != len(self.signals):
            raise ValueError('duplicate decision date')
        if len({x.event_id for x in self.events}) != len(self.events):
            raise ValueError('duplicate corporate action')
        if sum(len(x.candidates) for x in self.signals)>2000000:
            raise ValueError('signal cell budget exceeded')
        return self


class PortfolioRequest(Contract):
    scope: Scope
    dataset: DatasetRef
    pool_version: str = Field(min_length=1, max_length=200)
    start: date
    end: date
    top_n: int = Field(default=20, strict=True, ge=1, le=300)
    rebalance: Literal['week', 'month', 'explicit'] = 'week'
    weight: Literal['equal', 'mktcap', 'factor'] = 'equal'
    initial_cash: Decimal = Field(gt=0, le=Decimal('1000000000'), decimal_places=2)
    risk_free_annual: float = Field(default=0, gt=-1, le=1, allow_inf_nan=False)
    execution_version: Literal[EXECUTION_VERSION] = EXECUTION_VERSION
    cost_version: Literal[COST_VERSION] = COST_VERSION
    costs: CostSettings = Field(default_factory=CostSettings)
    qa_only: bool = Field(default=False, strict=True)
    _date = field_validator('start', 'end', mode='before')(explicit_date)

    @model_validator(mode='after')
    def valid_range(self):
        if self.start >= self.end:
            raise ValueError('start must precede end')
        return self


class DailyAccount(Contract):
    trade_date: date
    cash: Decimal
    position_value: Decimal
    receivables: Decimal
    equity: Decimal
    nav: Decimal
    daily_return: Decimal
    lots: list[PositionLot]
    _date = field_validator('trade_date', mode='before')(explicit_date)


class PortfolioResult(Contract):
    report_version: Literal['stock-portfolio-calculation-2026-10-25.v1'] = 'stock-portfolio-calculation-2026-10-25.v1'
    request: PortfolioRequest
    input_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence: ExecutionEvidence
    corporate_actions_version: Literal['stock-explicit-corporate-events-2026-10-25.v1'] = 'stock-explicit-corporate-events-2026-10-25.v1'
    validation_status: Literal['qa_only', 'execution_calculation_only']
    status: Literal['completed', 'partially_completed', 'not_qualified']
    blocking_reasons: list[str]
    daily: list[DailyAccount]
    targets: list[dict[str, Any]]
    executions: list[ExecutionResult]
    ledger: list[dict[str, Any]]
    metrics: dict[str, Any] | None = None
    limitations: list[str]
