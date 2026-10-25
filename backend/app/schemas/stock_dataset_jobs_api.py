from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.stock_research_v2 import Contract, DatasetBuild, DatasetRef
from app.schemas.stock_research_v2_jobs import JOB_KINDS, METHOD_VERSIONS, WORKFLOW_VERSIONS


class JobListQuery(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class CancelDatasetJob(Contract):
    pass


class DatasetJobSummary(Contract):
    id: UUID
    # Migration 021 widens the persisted kind/workflow/method allowlists; the
    # single-dataset semantics of the *result* guard below are unchanged.
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
    result: DatasetRef | None
    error_code: str | None = Field(max_length=100)

    @model_validator(mode="after")
    def validate_state(self):
        if (self.status == "completed") != (self.result is not None):
            raise ValueError("任务状态与结果不一致")
        if self.result is not None and self.result.qualification != "observation_only":
            raise ValueError("观察任务不能宣称研究或可成交资格")
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
    parameters: DatasetBuild


class DatasetJobPage(Contract):
    items: list[DatasetJobSummary] = Field(max_length=100)
    total: int = Field(strict=True, ge=0)
    has_more: bool = Field(strict=True)


class DatasetJobAvailability(Contract):
    accepting: bool
    research_enabled: Literal[False] = False
    reason: str = Field(max_length=500)
