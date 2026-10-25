from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictFloat, StrictInt, field_validator


FiniteNumber = Annotated[StrictFloat | StrictInt, Field(allow_inf_nan=False)]
JsonObject = dict[str, JsonValue]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SavedReportJob(ReportModel):
    id: UUID
    kind: Literal["analysis", "backtest"]
    title: str = Field(max_length=80)
    status: Literal["completed"]
    step: str = Field(max_length=32)
    progress: int = Field(ge=0, le=100)
    request_id: str = Field(max_length=32)
    attempts: int = Field(ge=0, le=1)
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    method_version: str | None = Field(max_length=64)
    workflow_version: str | None = Field(max_length=40)
    data_version: str | None = Field(max_length=64)
    has_result: bool
    error: JsonObject | None
    params: JsonObject
    data_manifest: JsonObject


class SavedResearchExport(ReportModel):
    export_version: Literal["stock-research-export-v1"] = "stock-research-export-v1"
    job: SavedReportJob
    result: JsonObject


class SavedResultSummary(ReportModel):
    metadata: JsonObject
    params: JsonObject
    metrics: dict[str, FiniteNumber | None] | None = Field(default=None, max_length=32)
    ic: dict[str, FiniteNumber | None] | None = Field(default=None, max_length=32)
    factor_policies: list[JsonObject] = Field(default_factory=list, max_length=100)


class SavedComparisonSide(ReportModel):
    job: SavedReportJob
    result: SavedResultSummary


class ReportCompatibility(ReportModel):
    comparable: bool
    same_method: bool
    same_data_version: bool
    same_workflow: bool
    same_requested_period: bool
    warnings: list[str] = Field(max_length=16)


class ComparedMetric(ReportModel):
    key: str = Field(max_length=64)
    label: str = Field(max_length=80)
    unit: Literal["percent", "ratio", "count"]
    left: FiniteNumber | None
    right: FiniteNumber | None
    delta: FiniteNumber | None


class SavedNavPoint(ReportModel):
    date: str
    nav: FiniteNumber = Field(gt=0)

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("净值日期必须为YYYY-MM-DD")
        return value


class ComparisonCurves(ReportModel):
    label: str = Field(max_length=40)
    left: list[SavedNavPoint] = Field(max_length=10000)
    right: list[SavedNavPoint] = Field(max_length=10000)


class SavedResearchComparison(ReportModel):
    comparison_version: Literal["stock-saved-comparison-v1"] = "stock-saved-comparison-v1"
    kind: Literal["analysis", "backtest"]
    left: SavedComparisonSide
    right: SavedComparisonSide
    compatibility: ReportCompatibility
    metrics: list[ComparedMetric] = Field(max_length=32)
    curves: ComparisonCurves
