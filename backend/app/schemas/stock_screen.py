from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StockScreenFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    industry: str = Field(default="", max_length=32)
    keyword: str = Field(default="", max_length=64)
    sort: Literal["code", "name", "change_pct"] = "code"
    direction: Literal["asc", "desc"] = "asc"
    data_only: bool = False
    min_close: float | None = Field(default=None, ge=0)
    max_close: float | None = Field(default=None, gt=0)
    min_change_pct: float | None = None
    max_change_pct: float | None = None
    min_volume: float | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def ordered_ranges(self) -> Self:
        for lower, upper in ((self.min_close, self.max_close), (self.min_change_pct, self.max_change_pct)):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("筛选下限不能大于上限")
        return self
