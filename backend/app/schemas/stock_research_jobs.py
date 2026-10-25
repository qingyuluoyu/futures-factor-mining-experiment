import hashlib
import json
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.stock_research import AnalysisBody, BacktestBody, PreprocessBody, _parse_date
from app.services.stock_factors.engine import METHOD_VERSION


WORKFLOW_VERSION = "stock-research-job-v1"
MAX_RUNTIME_SECONDS = 120
MAX_QUEUE_SECONDS = 900
MAX_ACTIVE_JOBS = 8
MAX_USER_ACTIVE_JOBS = 1
MAX_RESULT_BYTES = 8 * 1024 * 1024


class StrictPreprocess(PreprocessBody):
    model_config = ConfigDict(extra="forbid")


def validate_shanghai_period(body):
    if _parse_date(body.start_date) > _parse_date(body.end_date, datetime.now(ZoneInfo("Asia/Shanghai")).date()):
        raise ValueError("开始日期不得晚于结束日期")
    return body


class StrictAnalysisParameters(AnalysisBody):
    model_config = ConfigDict(allow_inf_nan=False, validate_default=True, extra="forbid")
    preprocess: StrictPreprocess = Field(default_factory=StrictPreprocess)

    @model_validator(mode="after")
    def validate_period(self):
        return validate_shanghai_period(self)


class StrictBacktestParameters(BacktestBody):
    model_config = ConfigDict(allow_inf_nan=False, validate_default=True, extra="forbid")
    preprocess: StrictPreprocess = Field(default_factory=StrictPreprocess)

    @model_validator(mode="after")
    def validate_period(self):
        return validate_shanghai_period(self)


class JobCreationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: UUID
    title: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("标题不得为空白")
        return value.strip() if value is not None else None


class CreateAnalysisJob(JobCreationBase):
    kind: Literal["analysis"]
    parameters: StrictAnalysisParameters


class CreateBacktestJob(JobCreationBase):
    kind: Literal["backtest"]
    parameters: StrictBacktestParameters


CreateResearchJob = Annotated[CreateAnalysisJob | CreateBacktestJob, Field(discriminator="kind")]


def canonical_job_parameters(body: CreateAnalysisJob | CreateBacktestJob, *, method_version: str | None = None, workflow_version: str | None = None) -> tuple[dict, str]:
    parameters = body.parameters.model_dump(mode="json")
    parameters["start_date"] = _parse_date(parameters["start_date"]).isoformat()
    parameters["end_date"] = _parse_date(parameters["end_date"]).isoformat() if parameters["end_date"] else ""
    canonical = {"kind": body.kind, "title": body.title, "parameters": parameters, "method_version": method_version if method_version is not None else METHOD_VERSION, "workflow_version": workflow_version if workflow_version is not None else WORKFLOW_VERSION}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return parameters, digest


def prepare_job_parameters(body: CreateAnalysisJob | CreateBacktestJob, *, today: date) -> tuple[dict, str]:
    parameters, digest = canonical_job_parameters(body)
    start = _parse_date(parameters["start_date"])
    end = _parse_date(parameters["end_date"], today)
    if start > end or end > today or (end - start).days > 3660:
        raise ValueError("研究区间不得超过3660天，结束日期不得晚于上海当前日期")
    return {**parameters, "end_date": end.isoformat()}, digest
