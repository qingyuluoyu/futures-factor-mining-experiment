from uuid import UUID
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


WORKFLOW_VERSION = "gp-persisted-worker-v1"
MAX_ACTIVE_JOBS = 8
MAX_USER_ACTIVE_JOBS = 2
MAX_GENERATION_SECONDS = 300
MAX_JOB_SECONDS = 1800
MAX_QUEUE_SECONDS = 900
MAX_RESULT_BYTES = 2 * 1024 * 1024
LEASE_SECONDS = 15
STOP_GRACE_SECONDS = 10


class CreateGpJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_id: UUID
    idempotency_key: UUID
    title: str | None = Field(None, min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("标题不能空白")
        return value.strip() if value is not None else None


class GpScope(BaseModel):
    market: Literal["futures"]
    mode: Literal["live", "virtual"]


class GpJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    input_id: UUID
    title: str
    status: Literal["pending", "running", "paused", "completed", "failed", "cancelled", "expired"]
    step: str
    generation: int = Field(ge=0, le=200)
    total_generations: int = Field(ge=1, le=200)
    attempts: int = Field(ge=0)
    runtime_seconds: float = Field(ge=0, le=MAX_JOB_SECONDS, allow_inf_nan=False)
    request_id: str
    method_version: str
    workflow_version: str
    error_code: str | None
    has_result: bool
    compute_stop_pending: bool
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    scope: GpScope
    validation_status: Literal["research_only"]
    source_certified: Literal[False]


class GpJobList(BaseModel):
    items: list[GpJobView]
    total: int = Field(ge=0)
    has_more: bool


class GpChampion(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    tokens: list[Annotated[int, Field(strict=True)]] = Field(min_length=1, max_length=1024)
    text: str = Field(max_length=8192)
    metrics: dict[str, Any]
    composite: float = Field(strict=True, allow_inf_nan=False)


FiniteMetric = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class GpFold(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    train: dict[str, FiniteMetric]
    test: dict[str, FiniteMetric]
    in_train: bool


class GpWalkForward(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    folds: list[GpFold] = Field(min_length=1, max_length=6)
    wf_stable: bool
    wf_mean_test_sortino: FiniteMetric
    wf_mean_test_ann: FiniteMetric
    wf_consistency: FiniteMetric
    n_folds: int = Field(ge=1, le=6)
    n_oos_folds: int = Field(ge=0, le=6)


class GpRegime(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bars: int = Field(ge=0, le=8000)
    sortino: FiniteMetric | None
    ann_ret: FiniteMetric | None


class GpDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    train_metrics: dict[str, FiniteMetric] | None = None
    test_metrics: dict[str, FiniteMetric] | None = None
    walk_forward: GpWalkForward | None = None
    regime: dict[Literal["trend_up", "trend_down", "chop"], GpRegime] | None = None
    oos_conservative: FiniteMetric | None = None
    trials: int = Field(ge=1, le=5000)
    pbo_proxy: FiniteMetric | None = Field(None, ge=0, le=1)
    overfit_warning: str | None = Field(None, min_length=1, max_length=512)


class GpReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    champions: list[GpChampion] = Field(max_length=100)
    generation: int = Field(ge=1, le=200)
    input_hash: str
    config_hash: str
    checkpoint_hash: str
    method_version: str
    workflow_version: str
    validation_status: Literal["research_only"]
    source_certified: Literal[False]
    job_id: UUID
    request_id: str
