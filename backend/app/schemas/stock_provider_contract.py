from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["daily", "valuation", "financial", "industry", "universe", "calendar", "benchmark", "action", "status"]
    instruments: list[str] = Field(min_length=1, max_length=1000)
    start: date
    end: date
    cursor: str | None = Field(default=None, max_length=512)

    @field_validator("instruments")
    @classmethod
    def identifiers(cls, values):
        import re
        if len(set(values)) != len(values) or any(not re.fullmatch(r"[0-9]{6}\.(?:SH|SZ|BJ)", value) for value in values):
            raise ValueError("证券标识重复或与交易所不匹配")
        return values

    @model_validator(mode="after")
    def period(self):
        if self.start > self.end:
            raise ValueError("日期区间倒置")
        if self.kind != "benchmark":
            import re
            if any(not re.fullmatch(r"(?:6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[489][0-9]{5}\.BJ)", value) for value in self.instruments):
                raise ValueError("股票代码与交易所不匹配")
        return self


class FetchBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    max_requests: int = Field(default=0, ge=0, le=10000, strict=True)
    max_rows: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    max_cost: Decimal = Field(default=Decimal(0), ge=0)
    currency: Literal["CNY", "USD"] = "CNY"
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("预算截止时间必须含时区")
        return value


class BudgetError(ValueError):
    pass


def authorize_attempt(budget: FetchBudget, *, attempts: int, rows: int, cost: Decimal, estimated_cost: Decimal | None, currency: str, now: datetime) -> None:
    if any(type(value) is not int or value < 0 for value in (attempts, rows)):
        raise BudgetError("调用和行数统计无效")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or now >= budget.deadline_at:
        raise BudgetError("预算时间已耗尽或无效")
    if attempts >= budget.max_requests or rows >= budget.max_rows:
        raise BudgetError("本次请求量或行数预算已耗尽")
    if currency != budget.currency or any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in (cost, estimated_cost)):
        raise BudgetError("费用未知、币种不一致或金额无效，不允许出网")
    if cost + estimated_cost > budget.max_cost:
        raise BudgetError("本次费用预算不足")
