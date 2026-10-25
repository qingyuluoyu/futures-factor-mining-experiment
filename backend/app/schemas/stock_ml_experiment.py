"""Internal chronological experiment contract; never confers holdout certification."""
from datetime import date
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, explicit_date


class WalkForwardSpec(Contract):
    sessions: list[date] = Field(min_length=6, max_length=20000)
    mode: Literal["expanding", "rolling"] = "expanding"
    warmup_sessions: int = Field(default=0, strict=True, ge=0, le=10000)
    train_sessions: int = Field(strict=True, ge=2, le=10000)
    validation_sessions: int = Field(strict=True, ge=1, le=5000)
    test_sessions: int = Field(strict=True, ge=1, le=5000)
    fold_count: int = Field(strict=True, ge=1, le=5)

    @field_validator("sessions", mode="before")
    @classmethod
    def dates(cls, value):
        if not isinstance(value, list):
            raise ValueError("必须传入明确交易日轴，不能以自然日推导")
        return [explicit_date(day) for day in value]

    @model_validator(mode="after")
    def windows(self):
        if self.sessions != sorted(set(self.sessions)):
            raise ValueError("交易日轴必须唯一且严格升序")
        needed = self.warmup_sessions + self.train_sessions + self.validation_sessions + self.test_sessions * self.fold_count
        if needed > len(self.sessions):
            raise ValueError("交易日不足以覆盖全部明确窗口；未自动缩短")
        return self
