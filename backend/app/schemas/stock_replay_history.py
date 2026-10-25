from datetime import date
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReplayHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, le=2147483647)
    status: Literal["all", "pending", "filled", "rejected", "cancelled"] = "all"
    direction: Literal["all", "buy", "sell"] = "all"
    code: str | None = Field(default=None, pattern=r"^(?:00|60)[0-9]{4}$", max_length=6)
    start: date | None = None
    end: date | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @field_validator("start", "end", mode="before")
    @classmethod
    def validate_date(cls, value):
        if value is not None and type(value) is not date and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)):
            raise ValueError("历史日期必须为ISO日期")
        return value

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("提交日期范围倒置")
        return self
