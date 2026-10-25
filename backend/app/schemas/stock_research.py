import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.stock_factors.catalog import FACTOR_SPECS


class PreprocessBody(BaseModel):
    winsorize: Literal["mad", "3sigma", "none"] = "mad"
    standardize: bool = True
    fillna: bool = True
    industry_neutral: bool = False
    mktcap_neutral: bool = False


def _parse_date(value: str, default: date | None = None) -> date:
    if value == "" and default is not None:
        return default
    if not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2})", value):
        raise ValueError("日期必须为YYYYMMDD、YYYY-MM-DD或YYYY/MM/DD")
    return date.fromisoformat(value.replace("/", "-"))


class ResearchPeriodBody(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, validate_default=True)

    start_date: str = Field(default="20180101", max_length=10)
    end_date: str = Field(default="", max_length=10)

    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, value: str) -> str:
        _parse_date(value)
        return value

    @field_validator("end_date")
    @classmethod
    def validate_end_date(cls, value: str) -> str:
        _parse_date(value, date.today())
        return value

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if _parse_date(self.start_date) > _parse_date(self.end_date, date.today()):
            raise ValueError("开始日期不得晚于结束日期")
        return self


def _validate_factor_ids(factor_ids: list[str]) -> list[str]:
    valid = {spec.id for spec in FACTOR_SPECS}
    unknown = [factor_id for factor_id in factor_ids if factor_id not in valid]
    if unknown:
        raise ValueError(f"未知因子: {', '.join(unknown)}")
    if len(set(factor_ids)) != len(factor_ids):
        raise ValueError("因子不得重复")
    return factor_ids


class AnalysisBody(ResearchPeriodBody):
    factor_id: str = Field(min_length=1, max_length=40)
    horizon: int = Field(default=20, ge=1, le=120)
    groups: int = Field(default=5, ge=2, le=10)
    preprocess: PreprocessBody = Field(default_factory=PreprocessBody)

    @field_validator("factor_id")
    @classmethod
    def validate_factor_id(cls, value: str) -> str:
        _validate_factor_ids([value])
        return value


class BacktestBody(ResearchPeriodBody):
    factor_ids: list[str] = Field(default_factory=lambda: ["ret_20d"], min_length=1, max_length=len(FACTOR_SPECS))
    rebalance: Literal["week", "month"] = "month"
    top_n: int = Field(default=20, ge=1, le=300)
    weight: Literal["equal", "mktcap", "factor"] = "equal"
    commission_rate: float = Field(default=0.0003, ge=0, le=0.01)
    slippage_rate: float = Field(default=0.001, ge=0, le=0.01)
    preprocess: PreprocessBody = Field(default_factory=PreprocessBody)

    @field_validator("factor_ids")
    @classmethod
    def validate_factor_ids(cls, value: list[str]) -> list[str]:
        return _validate_factor_ids(value)
