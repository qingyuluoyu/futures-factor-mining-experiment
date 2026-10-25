from datetime import date
import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.stock_research import _validate_factor_ids


class ResearchInputQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    factor_ids: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(min_length=1, max_length=9)
    mktcap_neutral: bool = False
    weight: Literal["equal", "mktcap", "factor"] = "equal"

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        if type(value) is not date and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)):
            raise ValueError("输入检查日期必须为ISO交易日")
        return value

    @field_validator("factor_ids")
    @classmethod
    def validate_factors(cls, value):
        return sorted(_validate_factor_ids(value))

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.start_date > self.end_date or (self.end_date - self.start_date).days > 3660:
            raise ValueError("输入检查区间无效或超过3660天")
        return self
