from datetime import date
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FundamentalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9]{6}$", max_length=6)
    kind: Literal["financial", "valuation"] = "financial"
    as_of: date
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @field_validator("as_of", mode="before")
    @classmethod
    def validate_as_of(cls, value):
        if type(value) is not date and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)):
            raise ValueError("截止日期必须为ISO日期")
        return value
