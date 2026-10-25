from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator, model_validator

from app.schemas.stock_benchmark_v2 import IndexSeries
from app.schemas.stock_dataset_inspection import ResearchRequirements
from app.schemas.stock_ml import TimeSplit, TrainingParameters
from app.schemas.stock_ml_experiment import WalkForwardSpec
from app.schemas.stock_portfolio_v2 import CostSettings
from app.schemas.stock_research_v2 import Contract, DatasetBuild, DatasetRef, Scope, SHA256, aware_utc, explicit_date

WORKFLOW_VERSION = "stock-v2-dataset-job-v1"
RESEARCH_WORKFLOW_VERSION = "stock-v2-research-task-v1"
WORKFLOW_VERSIONS = (WORKFLOW_VERSION, RESEARCH_WORKFLOW_VERSION)
METHOD_VERSION = "stock-observation-manifest-v1"
RESEARCH_METHOD_VERSION = "stock-research-task-v1"
METHOD_VERSIONS = (METHOD_VERSION, RESEARCH_METHOD_VERSION)
# Migration 021 widens ck_stock_v2_job_kind to exactly this allowlist.
JOB_KINDS = ("dataset", "research_ic", "backtest", "benchmark", "ml_train", "report")
ACTIVE = ("pending", "running")
TERMINAL = ("completed", "failed", "cancelled", "expired")
# The observation dataset contract keeps its original 300s ceiling; only the
# research kernels, which may train a native LambdaRank model, reach 900s.
DATASET_MAX_RUNTIME = 300
RESEARCH_MAX_RUNTIME = 900


class CreateDatasetJob(Contract):
    kind: Literal["dataset"] = "dataset"
    idempotency_key: UUID
    parameters: DatasetBuild
    max_runtime_seconds: int = Field(default=120, strict=True, ge=30, le=DATASET_MAX_RUNTIME)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class ResearchICParameters(Contract):
    kind: Literal["research_ic"] = "research_ic"
    dataset: DatasetRef
    factors: ResearchRequirements
    horizon: int = Field(default=20, strict=True, ge=1, le=250)
    groups: int = Field(default=5, strict=True, ge=2, le=20)
    decay_lags: list[int] = Field(default=(1, 5, 10, 20), max_length=8)

    @field_validator("decay_lags")
    @classmethod
    def lags(cls, values):
        if len(set(values)) != len(values) or any(value < 1 for value in values):
            raise ValueError("衰减滞后不能重复或小于1")
        return sorted(values)


class BacktestParameters(Contract):
    kind: Literal["backtest"] = "backtest"
    dataset: DatasetRef
    factors: ResearchRequirements
    pool_version: str = Field(pattern=SHA256)
    start: date
    end: date
    top_n: int = Field(default=20, strict=True, ge=1, le=1000)
    rebalance: Literal["daily", "weekly", "monthly"] = "monthly"
    weight: Literal["equal", "factor", "mktcap"] = "equal"
    initial_cash: Decimal = Field(default=Decimal("1000000"), gt=0, le=Decimal("1e14"), decimal_places=2)
    risk_free_annual: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("1"), decimal_places=6)
    costs: CostSettings = CostSettings()
    qa_only: bool = True
    _dates = field_validator("start", "end", mode="before")(explicit_date)

    @model_validator(mode="after")
    def period(self):
        if self.start > self.end:
            raise ValueError("回测区间倒置")
        return self


class BenchmarkParameters(Contract):
    kind: Literal["benchmark"] = "benchmark"
    dataset: DatasetRef
    strategy_artifact_sha256: str = Field(pattern=SHA256)
    index: IndexSeries


class MLTrainParameters(Contract):
    kind: Literal["ml_train"] = "ml_train"
    dataset: DatasetRef
    factors: ResearchRequirements
    split: WalkForwardSpec
    training: TrainingParameters = TrainingParameters()
    qa_only: bool = True


class ReportParameters(Contract):
    kind: Literal["report"] = "report"
    dataset: DatasetRef
    factors: ResearchRequirements
    split: TimeSplit
    horizon: int = Field(default=20, strict=True, ge=1, le=250)
    groups: int = Field(default=5, strict=True, ge=2, le=20)
    top_n: int = Field(default=20, strict=True, ge=1, le=1000)
    training: TrainingParameters = TrainingParameters()
    qa_only: bool = True


TASK_PARAMETERS = (ResearchICParameters, BacktestParameters, BenchmarkParameters, MLTrainParameters, ReportParameters)
# Single source of truth for the claim payload union: the tag is the persisted
# job kind, and an observation build is the only payload without its own ``kind``.
PARAMETER_MODELS = {"dataset": DatasetBuild, "research_ic": ResearchICParameters, "backtest": BacktestParameters,
    "benchmark": BenchmarkParameters, "ml_train": MLTrainParameters, "report": ReportParameters}
assert tuple(PARAMETER_MODELS) == JOB_KINDS


def parameter_tag(parameters):
    """Discriminator for a claim payload: never guessed from the request body.

    ``DatasetBuild`` has no ``kind`` of its own, so an observation payload is
    tagged from the field that only it declares.  Anything else must declare its
    own tag; an unrecognised body is refused instead of falling back to a kind.
    """
    if isinstance(parameters, DatasetBuild):
        return "dataset"
    if isinstance(parameters, BaseModel):
        return str(getattr(parameters, "kind", ""))
    if isinstance(parameters, dict):
        return "dataset" if "source_ids" in parameters else str(parameters.get("kind") or "")
    return ""


TaskParameters = Annotated[Union[tuple(Annotated[model, Tag(tag)] for tag, model in PARAMETER_MODELS.items())],
    Discriminator(parameter_tag)]


class CreateResearchICJob(Contract):
    kind: Literal["research_ic"] = "research_ic"
    idempotency_key: UUID
    parameters: ResearchICParameters
    max_runtime_seconds: int = Field(default=300, strict=True, ge=30, le=RESEARCH_MAX_RUNTIME)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class CreateBacktestJob(Contract):
    kind: Literal["backtest"] = "backtest"
    idempotency_key: UUID
    parameters: BacktestParameters
    max_runtime_seconds: int = Field(default=600, strict=True, ge=30, le=RESEARCH_MAX_RUNTIME)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class CreateBenchmarkJob(Contract):
    kind: Literal["benchmark"] = "benchmark"
    idempotency_key: UUID
    parameters: BenchmarkParameters
    max_runtime_seconds: int = Field(default=300, strict=True, ge=30, le=RESEARCH_MAX_RUNTIME)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class CreateMLTrainJob(Contract):
    kind: Literal["ml_train"] = "ml_train"
    idempotency_key: UUID
    parameters: MLTrainParameters
    max_runtime_seconds: int = Field(default=900, strict=True, ge=30, le=RESEARCH_MAX_RUNTIME)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class CreateReportJob(Contract):
    kind: Literal["report"] = "report"
    idempotency_key: UUID
    parameters: ReportParameters
    max_runtime_seconds: int = Field(default=900, strict=True, ge=30, le=RESEARCH_MAX_RUNTIME)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class WorkerLease(Contract):
    instance_id: UUID
    token: UUID
    workflow_version: Literal["stock-v2-dataset-job-v1"] = WORKFLOW_VERSION


class DatasetClaim(Contract):
    job_id: UUID
    kind: Literal["dataset", "research_ic", "backtest", "benchmark", "ml_train", "report"] = "dataset"
    scope: Scope
    worker: WorkerLease
    token: UUID
    parameters: TaskParameters
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    deadline_at: datetime
    _aware = field_validator("deadline_at")(aware_utc)

    @model_validator(mode="after")
    def matched(self):
        # The authoritative kind is the persisted job row; this only stops a claim
        # from ever carrying another kind's payload.
        if parameter_tag(self.parameters) != self.kind:
            raise ValueError("领取参数与任务类型不一致")
        return self
