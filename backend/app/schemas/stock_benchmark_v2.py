"""Strict index and comparison contracts; evidence references never certify a source."""
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, explicit_date
from app.schemas.stock_portfolio_v2 import PortfolioRequest


class BenchmarkSpec(Contract):
    instrument_id: str = Field(pattern=r'^(?:000[0-9]{3}\.SH|399[0-9]{3}\.SZ)$')
    kind: Literal['price_index','total_return_index']
    total_return_basis: Literal['not_applicable','gross_reinvested','net_reinvested']
    currency: Literal['CNY']
    market: Literal['CN_A']
    timezone: Literal['Asia/Shanghai']
    frequency: Literal['1d']
    level_unit: Literal['point']
    provider: str = Field(min_length=1,max_length=100)
    source_reference: str = Field(min_length=1,max_length=200)
    authorization_reference: str = Field(min_length=1,max_length=200)
    methodology_reference: str = Field(min_length=1,max_length=200)
    calendar_reference: str = Field(min_length=1,max_length=200)
    data_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    qualification: Literal['unverified','verified']
    anchor: Literal['next_session_open']

    @field_validator('provider','source_reference','authorization_reference','methodology_reference','calendar_reference')
    @classmethod
    def nonblank_reference(cls,value):
        if not value.strip():raise ValueError('blank reference is not evidence')
        return value.strip()

    @model_validator(mode='after')
    def explicit_return_basis(self):
        if (self.kind=='price_index')!=(self.total_return_basis=='not_applicable'):
            raise ValueError('index total-return basis is missing or inconsistent')
        return self


class IndexPoint(Contract):
    trade_date: date
    open: Decimal | None = Field(default=None,gt=0,le=Decimal('1e12'))
    close: Decimal | None = Field(default=None,gt=0,le=Decimal('1e12'))
    _date=field_validator('trade_date',mode='before')(explicit_date)


class IndexSeries(Contract):
    spec: BenchmarkSpec
    rows: list[IndexPoint] = Field(min_length=2,max_length=20000)

    @model_validator(mode='after')
    def ordered_unique(self):
        dates=[x.trade_date for x in self.rows]
        if dates!=sorted(set(dates)):
            raise ValueError('index dates must be ordered and unique')
        return self


class IndexComparisonDay(Contract):
    trade_date: date
    strategy_nav: Decimal
    benchmark_nav: Decimal
    strategy_return: Decimal
    benchmark_return: Decimal
    return_difference: Decimal
    relative_wealth_return: Decimal
    _date=field_validator('trade_date',mode='before')(explicit_date)


class BenchmarkComparison(Contract):
    report_version: Literal['stock-index-comparison-2026-10-25.v1']='stock-index-comparison-2026-10-25.v1'
    comparison_method: Literal['strict-calendar-next-open-244-v1']='strict-calendar-next-open-244-v1'
    validation_status: Literal['qa_only','calculation_only']
    source_qualification: Literal['unverified','verified']
    economic_excess_available: Literal[False]=False
    strategy_parameters: PortfolioRequest
    strategy_input_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    strategy_content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    spec: BenchmarkSpec
    index_content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    index_rows: list[IndexPoint]
    daily: list[IndexComparisonDay]
    strategy_metrics: dict[str,Any]
    benchmark_metrics: dict[str,Any]
    active_metrics: dict[str,Any]
    warnings: list[str]


class StrategyComparisonDay(Contract):
    trade_date: date
    left_nav: Decimal
    right_nav: Decimal
    left_return: Decimal
    right_return: Decimal
    return_difference: Decimal
    relative_wealth_return: Decimal
    _date=field_validator('trade_date',mode='before')(explicit_date)


class StrategyComparison(Contract):
    report_version: Literal['stock-strategy-comparison-2026-10-25.v1']='stock-strategy-comparison-2026-10-25.v1'
    comparison_method: Literal['strict-scope-calendar-cost-244-v1']='strict-scope-calendar-cost-244-v1'
    validation_status: Literal['qa_only','calculation_only']
    left_parameters: PortfolioRequest
    right_parameters: PortfolioRequest
    left_input_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    right_input_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    left_content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    right_content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    daily: list[StrategyComparisonDay]
    left_metrics: dict[str,Any]
    right_metrics: dict[str,Any]
    active_metrics: dict[str,Any]
    warnings: list[str]
