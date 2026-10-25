from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_job_id: UUID
    expected_data_version: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")
    idempotency_key: UUID
    reason: str = Field(strict=True, min_length=8, max_length=160)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value):
        return value.strip() if isinstance(value, str) else value
