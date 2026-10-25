from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SHA256 = r"^[a-f0-9]{64}$"
EQUITY = r"(?:6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[489][0-9]{5}\.BJ)"
FIELD_UNITS = {"open": "CNY/share", "high": "CNY/share", "low": "CNY/share", "close": "CNY/share", "pre_close": "CNY/share", "volume": "share", "amount": "CNY", "adj_factor": "ratio", "pe_ttm": "ratio", "pb": "ratio", "turnover": "percent", "marketcap": "CNY", "revenue_yoy": "percent", "netprofit_yoy": "percent", "roe": "percent", "gross_margin": "percent"}
KIND_FIELDS = {"daily": {"open", "high", "low", "close", "pre_close", "volume", "amount", "adj_factor"}, "valuation": {"pe_ttm", "pb", "turnover", "marketcap"}, "financial": {"revenue_yoy", "netprofit_yoy", "roe", "gross_margin"}}


def equity_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(EQUITY, value):
        raise ValueError("股票标准标识与交易所不匹配")
    return value


def aware_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须带明确时区")
    return value.astimezone(timezone.utc)


def explicit_datetime(value):
    if value is not None and not isinstance(value, (datetime, str)):
        raise ValueError("时间必须明确提供带时区的ISO文本或datetime，不能猜测epoch单位")
    return value


def explicit_date(value):
    if value is None or type(value) is date or (isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)):
        return value
    raise ValueError("日期必须为YYYY-MM-DD或date，不能猜测epoch或截断时刻")


def precise_values(values):
    if not isinstance(values, dict) or not values or len(values) > 16 or any(key not in FIELD_UNITS for key in values):
        raise ValueError("数值字段为空或未知")
    for value in values.values():
        if value is None:
            continue
        if type(value) is not str or len(value) > 80 or not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
            raise ValueError("原始数值必须为保留精度的十进制字符串或显式缺失")
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError("十进制数值无效") from None
        if not number.is_finite() or abs(number) >= Decimal("1e30"):
            raise ValueError("数值超出有限安全范围")
    return values


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scope(Contract):
    owner_id: UUID
    trading_mode: Literal["virtual", "live"]


class ArtifactRef(Contract):
    artifact_id: UUID
    sha256: str = Field(pattern=SHA256)
    byte_size: int = Field(strict=True, gt=0, le=64 * 1024 * 1024)
    relative_path: str = Field(pattern=r"^[a-f0-9]{64}\.json$")


class DatasetRef(Contract):
    dataset_id: UUID
    data_version: str = Field(pattern=SHA256)
    qualification: Literal["observation_only", "research_only", "backtest_ready"]


class SourceRef(Contract):
    source_id: UUID
    artifact: ArtifactRef
    kind: Literal["daily", "valuation", "financial"]


class SourceObservation(Contract):
    instrument_id: str
    effective_date: date
    report_period: date | None = None
    announced_at: datetime | None = None
    announced_on: date | None = None
    available_at: datetime | None = None
    vintage: Literal["original_filing", "revision_filing", "unverified"] = "unverified"
    vintage_reference: str | None = Field(default=None, min_length=1, max_length=200)
    values: dict[str, str | None] = Field(min_length=1, max_length=16)

    _identity = field_validator("instrument_id")(equity_identifier)
    _values = field_validator("values", mode="before")(precise_values)
    _explicit_times = field_validator("announced_at", "available_at", mode="before")(explicit_datetime)
    _explicit_dates = field_validator("effective_date", "report_period", "announced_on", mode="before")(explicit_date)

    @field_validator("announced_at", "available_at")
    @classmethod
    def times(cls, value):
        return aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def constraints(self):
        from zoneinfo import ZoneInfo
        if self.announced_at is not None and self.announced_on is not None:
            raise ValueError("公告日期与精确时刻不能混填")
        if self.vintage != "unverified" and self.vintage_reference is None:
            raise ValueError("原始/修订披露必须保留具体证据引用")
        if self.available_at is not None and self.announced_at is not None and self.available_at < self.announced_at:
            raise ValueError("数据可得时间早于公告")
        published = self.announced_at.astimezone(ZoneInfo("Asia/Shanghai")).date() if self.announced_at is not None else self.announced_on
        if self.report_period is not None and (self.report_period > self.effective_date or (published is not None and self.report_period > published)):
            raise ValueError("报告期晚于观察日期或公告日期")
        for field, value in self.values.items():
            if value is None:
                continue
            if field in {"open", "high", "low", "close", "pre_close", "adj_factor"} and Decimal(value) <= 0:
                raise ValueError("价格与调整因子必须为正；缺失不得补1")
            if field in {"volume", "amount", "turnover", "marketcap"} and Decimal(value) < 0:
                raise ValueError("成交量/金额/换手/市值不能为负")
        if all(self.values.get(field) is not None for field in ("open", "high", "low", "close")):
            opening, high, low, close = (Decimal(self.values[field]) for field in ("open", "high", "low", "close"))
            if not low <= min(opening, close) <= max(opening, close) <= high:
                raise ValueError("OHLC范围冲突")
        return self


class SourcePage(Contract):
    normalization_version: Literal["stock-normalized-observation-v1"] = "stock-normalized-observation-v1"
    provider: Literal["teajoin"]
    source_method: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    authorization_reference: str = Field(min_length=1, max_length=200)
    source_schema_reference: str = Field(min_length=1, max_length=200)
    market: Literal["CN_A"] = "CN_A"
    currency: Literal["CNY"] = "CNY"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    calendar_reference: str = Field(default="unverified", min_length=1, max_length=200)
    frequency: Literal["1d", "disclosure"] = "1d"
    kind: Literal["daily", "valuation", "financial"]
    financial_basis: Literal["not_applicable", "consolidated", "parent", "vendor_reported"] = "not_applicable"
    stream_key: str = Field(min_length=1, max_length=200)
    revises_id: UUID | None = None
    collected_at: datetime
    price_adjustment: Literal["raw", "qfq", "not_applicable"]
    adjustment_anchor: date | None = None
    units: dict[str, str] = Field(min_length=1, max_length=16)
    observations: list[SourceObservation] = Field(min_length=1, max_length=10000)

    _collected = field_validator("collected_at")(aware_utc)
    _explicit_collected = field_validator("collected_at", mode="before")(explicit_datetime)
    _explicit_anchor = field_validator("adjustment_anchor", mode="before")(explicit_date)

    @model_validator(mode="after")
    def metadata(self):
        from zoneinfo import ZoneInfo
        if self.price_adjustment == "qfq" and self.adjustment_anchor is None:
            raise ValueError("前复权必须指定固定锚点")
        if self.price_adjustment != "qfq" and self.adjustment_anchor is not None:
            raise ValueError("非前复权数据不得混入锚点")
        if self.adjustment_anchor is not None and self.adjustment_anchor > self.collected_at.astimezone(ZoneInfo(self.timezone)).date():
            raise ValueError("复权锚点晚于采集日期")
        if self.kind == "daily" and self.price_adjustment == "not_applicable":
            raise ValueError("日线必须明确原价或固定复权")
        if self.kind != "daily" and self.price_adjustment != "not_applicable":
            raise ValueError("非价格批次不得声明价格复权")
        if self.kind == "financial" and (self.frequency != "disclosure" or self.financial_basis == "not_applicable"):
            raise ValueError("财务批次必须明确披露频率与报表口径")
        if self.kind != "financial" and (self.frequency != "1d" or self.financial_basis != "not_applicable"):
            raise ValueError("非财务批次的频率或报表口径冲突")
        seen, used = set(), set()
        for row in self.observations:
            key = (row.instrument_id, row.effective_date, row.report_period, row.announced_at, row.announced_on)
            if key in seen:
                raise ValueError("同批次重复或冲突观察值")
            seen.add(key)
            used.update(row.values)
            if not set(row.values) <= KIND_FIELDS[self.kind]:
                raise ValueError("字段与数据类别不匹配")
            if self.kind != "financial" and row.vintage != "unverified":
                raise ValueError("非财务数据不能声明原始财报版本")
            if row.effective_date > self.collected_at.astimezone(ZoneInfo(self.timezone)).date():
                raise ValueError("观察日期晚于采集时间")
            if any(value is not None and value > self.collected_at for value in (row.announced_at, row.available_at)):
                raise ValueError("公告/可得时间晚于采集时间")
            if row.announced_on is not None and row.announced_on > self.collected_at.astimezone(ZoneInfo(self.timezone)).date():
                raise ValueError("公告日期晚于采集日期")
            if self.kind == "financial" and row.report_period is None:
                raise ValueError("财务缺少报告期")
        if set(self.units) != used or any(self.units[field] != FIELD_UNITS[field] for field in used):
            raise ValueError("字段单位缺失、未知或未经规范化")
        return self


class DatasetBuild(Contract):
    source_ids: list[UUID] = Field(min_length=1, max_length=1000)
    start: date
    end: date
    warmup_start: date
    pool_id: str = Field(min_length=1, max_length=100)
    pool_version: str = Field(pattern=SHA256)
    instruments: list[str] = Field(min_length=1, max_length=10000)
    _explicit_dates = field_validator("start", "end", "warmup_start", mode="before")(explicit_date)

    @field_validator("instruments")
    @classmethod
    def identifiers(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("股票池证券重复")
        return sorted(equity_identifier(value) for value in values)

    @field_validator("source_ids")
    @classmethod
    def sources(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("来源引用重复")
        return sorted(values, key=str)

    @model_validator(mode="after")
    def period(self):
        if not self.warmup_start <= self.start <= self.end:
            raise ValueError("暖机与实际区间倒置")
        return self
