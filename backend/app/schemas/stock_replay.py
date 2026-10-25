from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReplayContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateReplayAccount(ReplayContract):
    idempotency_key: UUID
    name: str = Field(min_length=1, max_length=64)
    initial_cash: Decimal = Field(ge=100, le=1000000000, max_digits=12, decimal_places=2)
    start_date: date = Field(ge=date(2018, 1, 1))
    dataset_ids: list[UUID] = Field(min_length=1, max_length=20)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0, le=Decimal("0.01"), decimal_places=6)
    min_commission: Decimal = Field(default=Decimal("5"), ge=0, le=100, decimal_places=2)
    slippage_rate: Decimal = Field(default=Decimal("0.001"), ge=0, le=Decimal("0.02"), decimal_places=6)

    @model_validator(mode="after")
    def unique_datasets(self):
        if len(set(self.dataset_ids)) != len(self.dataset_ids):
            raise ValueError("数据快照不得重复")
        return self


class ReplayMutation(ReplayContract):
    idempotency_key: UUID
    expected_revision: int = Field(ge=0)


class SubmitReplayOrder(ReplayMutation):
    code: str = Field(pattern=r"^(?:60|00)[0-9]{4}$")
    direction: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=1000000, multiple_of=100)
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=2)

    @model_validator(mode="after")
    def valid_limit(self):
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("仅限价委托必须填写限价")
        return self
