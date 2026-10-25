from datetime import date
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, DatasetRef, SHA256, equity_identifier, explicit_date

FieldName = Literal["open", "high", "low", "close", "pre_close", "volume", "amount", "adj_factor", "pe_ttm", "pb", "turnover", "marketcap", "revenue_yoy", "netprofit_yoy", "roe", "gross_margin"]
BuiltinFactor = Literal["pe_ttm", "pb", "revenue_yoy", "netprofit_yoy", "roe", "gross_margin", "ret_20d", "vol_20d", "turnover_20d"]


class PanelQuery(Contract):
    fields: list[FieldName] = Field(min_length=1, max_length=16)
    start: date | None = None
    end: date | None = None
    instruments: list[str] | None = Field(default=None, min_length=1, max_length=10000)
    include_warmup: bool = Field(default=False, strict=True)
    batch_rows: int = Field(default=128, strict=True, ge=1, le=1000)
    _dates = field_validator("start", "end", mode="before")(explicit_date)

    @field_validator("fields", "instruments")
    @classmethod
    def unique(cls, values, info):
        if values is None:
            return None
        if len(values) != len(set(values)):
            raise ValueError("字段或证券不能重复")
        return sorted(equity_identifier(value) if info.field_name == "instruments" else value for value in values)

    @model_validator(mode="after")
    def period(self):
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("读取日期区间倒置")
        return self


class CustomFactorReference(Contract):
    id: UUID
    revision: int = Field(strict=True, ge=1, le=2147483647)
    expression_version: str = Field(pattern=SHA256)


class ResearchRequirements(Contract):
    factor_ids: list[BuiltinFactor] = Field(default_factory=lambda: ["ret_20d"], max_length=9)
    custom_factors: list[CustomFactorReference] = Field(default_factory=list, max_length=16)
    action: Literal["analysis", "backtest", "rotation"] = "analysis"
    industry_neutral: bool = Field(default=False, strict=True)
    mktcap_neutral: bool = Field(default=False, strict=True)
    weight: Literal["equal", "mktcap", "factor"] = "equal"

    @model_validator(mode="after")
    def factors(self):
        if not self.factor_ids and not self.custom_factors:
            raise ValueError("至少选择一个内置或固定版本自定义因子")
        if len(set(self.factor_ids)) != len(self.factor_ids) or len({(item.id, item.revision) for item in self.custom_factors}) != len(self.custom_factors):
            raise ValueError("所选因子不能重复")
        return self


class DatasetCatalogItem(DatasetRef):
    artifact_state: Literal["ready", "quarantined"]
    integrity: Literal["not_checked"]
    created_at: str


class DatasetCatalogPage(Contract):
    items: list[DatasetCatalogItem] = Field(max_length=100)
    total: int = Field(ge=0)
    has_more: bool


class DatasetSourceSummary(Contract):
    source_id: UUID
    sha256: str = Field(pattern=SHA256)
    byte_size: int = Field(gt=0, le=64 * 1024 * 1024)
    row_count: int = Field(ge=1, le=10000)
    kind: Literal["daily", "valuation", "financial"]


class FieldCoverage(Contract):
    """Per-field eligible-observation counts; never a fabricated coverage percentage."""
    raw_observations: int = Field(ge=0)
    eligible_observations: int = Field(ge=0)
    source_null: int = Field(ge=0)
    excluded_domain: int = Field(ge=0)
    basis_unverified: int = Field(ge=0)
    temporal_unverified: int = Field(ge=0)


class SourceAuthorizationEvidence(Contract):
    """Version-bound record that a fixed source batch's data use is authorized.

    Supplying this object is the only way to evidence source authorization; it is
    never inferred from a request flag and never defaults to granted.
    """
    record: str = Field(min_length=1, max_length=200)
    evidence_version: str = Field(pattern=SHA256)
    scope: Literal["research", "backtest"]


class CapabilityAssessment(Contract):
    """One per-action capability, resolved against the rules actually requested."""
    name: Literal["analysis", "portfolio", "ml", "rotation"]
    required_fields: list[str]
    coverage: dict[str, FieldCoverage]
    missing_reasons: list[str]
    allowed_scope: Literal["none", "qa_only", "research_only", "backtest_ready"]
    limitations: list[str] = Field(max_length=20)

    @field_validator("required_fields")
    @classmethod
    def fields(cls, values):
        if len(values) != len(set(values)) or any(not re.fullmatch(r"[a-z_][a-z_0-9]{0,31}", value) for value in values):
            raise ValueError("能力依赖字段重复或标识无效")
        return sorted(values)

    @field_validator("missing_reasons")
    @classmethod
    def reasons(cls, values):
        if len(values) != len(set(values)) or any(not re.fullmatch(r"[a-z_][a-z_0-9_]*(?::[^\s:]{1,160})?", value) for value in values):
            raise ValueError("阻断原因重复或格式无效")
        return sorted(values)


class DatasetDescription(Contract):
    reference: DatasetRef
    manifest_version: Literal["stock-observation-manifest-v1"]
    start: date
    end: date
    warmup_start: date
    pool_id: str = Field(min_length=1, max_length=100)
    pool_version: str = Field(pattern=SHA256)
    instruments: list[str] = Field(min_length=1, max_length=10000)
    sources: list[DatasetSourceSummary] = Field(min_length=1, max_length=1000)
    integrity: Literal["manifest_verified_sources_not_checked"]
    research_enabled: bool
    limitations: list[str] = Field(max_length=20)


class DatasetAvailability(Contract):
    catalog_enabled: bool
    detail_enabled: bool
    inspection_enabled: bool
    research_enabled: bool
    parquet_enabled: bool
    reason: str


class DatasetListQuery(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class DatasetLookup(Contract):
    data_version: str = Field(pattern=SHA256)
    qualification: Literal["observation_only", "research_only", "backtest_ready"] = "observation_only"
