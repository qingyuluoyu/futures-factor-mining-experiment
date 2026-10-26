from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.stock_research_v2 import SHA256, Contract, DatasetRef
from app.schemas.stock_research_v2_jobs import JOB_KINDS, METHOD_VERSIONS, WORKFLOW_VERSIONS, TaskParameters


class JobListQuery(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class CancelDatasetJob(Contract):
    pass


class DatasetJobResult(DatasetRef):
    """The 021 dataset branch, and the only result shape that restates a dataset.

    ``ck_stock_v2_job_result`` pins this branch to ``observation_only``, and the two
    research branches are required to leave all three dataset columns NULL.  So a
    research job's result cannot restate a dataset qualification and therefore cannot
    claim one: a run never upgrades the dataset it was built from.
    """

    @model_validator(mode="after")
    def observation_only(self):
        if self.qualification != "observation_only":
            raise ValueError("观察任务不能宣称研究或可成交资格")
        return self


class ExperimentJobResult(Contract):
    """The 021 experiment branch: a 019 experiment row, referenced and never re-certified.

    It carries the identifier only.  The experiment's own report, grade and dataset
    binding live on the 019 row and are read through the model API, so publishing a job
    result can never restate — and so never raise — a validation grade.
    """
    experiment_id: UUID


class ReportJobResult(Contract):
    """The 021 report branch: the published artifact row and its content digest."""
    artifact_id: UUID
    report_sha256: str = Field(pattern=SHA256)


# The three branches of ``ck_stock_v2_job_result`` are mutually exclusive and each is
# distinguished by its own required fields, so exactly one of these is ever present.
# ``Contract`` forbids extra fields, so a report payload cannot be read as a dataset
# reference that is merely missing its dataset columns.
TaskJobResult = DatasetJobResult | ExperimentJobResult | ReportJobResult


class DatasetJobSummary(Contract):
    id: UUID
    # Migration 021 widens the persisted kind/workflow/method allowlists and replaces
    # the single-dataset result with the three branches above; the state machine below
    # is what keeps a response from claiming a result its own status cannot carry.
    kind: Literal[*JOB_KINDS]
    status: Literal["pending", "running", "completed", "failed", "cancelled", "expired"]
    step: str = Field(max_length=64)
    attempts: int = Field(strict=True, ge=0, le=1)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    queue_expires_at: datetime
    method_version: Literal[*METHOD_VERSIONS]
    workflow_version: Literal[*WORKFLOW_VERSIONS]
    result: TaskJobResult | None
    error_code: str | None = Field(max_length=100)

    @model_validator(mode="after")
    def validate_state(self):
        if (self.status == "completed") != (self.result is not None):
            raise ValueError("任务状态与结果不一致")
        if (self.status in {"pending", "running"}) != (self.finished_at is None):
            raise ValueError("任务结束时间与状态不一致")
        timestamps = [self.created_at, self.updated_at, self.started_at, self.finished_at, self.deadline_at, self.queue_expires_at]
        if any(value is not None and value.utcoffset() is None for value in timestamps):
            raise ValueError("任务时间缺少时区")
        if self.status == "running" and (self.attempts != 1 or self.started_at is None or self.deadline_at is None):
            raise ValueError("运行任务缺少领取记录或运行期限")
        if self.status == "pending" and (self.attempts != 0 or self.started_at is not None or self.deadline_at is not None):
            raise ValueError("待执行任务不应含运行记录")
        if self.status == "completed" and (self.attempts != 1 or self.started_at is None):
            raise ValueError("完成任务缺少执行记录")
        return self


class DatasetJobDetail(DatasetJobSummary):
    # The detail view returns ``job.parameters["command"]``: an observation build for
    # the dataset kind and one of the five research parameter models otherwise.  Using
    # the same discriminated union the claim uses keeps a research job's detail
    # readable instead of failing validation the moment 021 widened the kind allowlist.
    parameters: TaskParameters


class DatasetJobPage(Contract):
    items: list[DatasetJobSummary] = Field(max_length=100)
    total: int = Field(strict=True, ge=0)
    has_more: bool = Field(strict=True)


class DatasetJobAvailability(Contract):
    accepting: bool
    research_enabled: Literal[False] = False
    reason: str = Field(max_length=500)
