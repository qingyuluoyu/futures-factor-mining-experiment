from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.stock_research_v2 import Contract, DatasetBuild, Scope, aware_utc

WORKFLOW_VERSION = "stock-v2-dataset-job-v1"
METHOD_VERSION = "stock-observation-manifest-v1"
ACTIVE = ("pending", "running")
TERMINAL = ("completed", "failed", "cancelled", "expired")


class CreateDatasetJob(Contract):
    kind: Literal["dataset"] = "dataset"
    idempotency_key: UUID
    parameters: DatasetBuild
    max_runtime_seconds: int = Field(default=120, strict=True, ge=30, le=300)
    max_queue_seconds: int = Field(default=300, strict=True, ge=30, le=900)


class WorkerLease(Contract):
    instance_id: UUID
    token: UUID
    workflow_version: Literal["stock-v2-dataset-job-v1"] = WORKFLOW_VERSION


class DatasetClaim(Contract):
    job_id: UUID
    scope: Scope
    worker: WorkerLease
    token: UUID
    parameters: DatasetBuild
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    deadline_at: datetime
    _aware = field_validator("deadline_at")(aware_utc)
