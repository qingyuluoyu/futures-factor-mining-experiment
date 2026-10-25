from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EodGapQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["all", "missing", "unknown"] = "all"
    keyword: str = Field(default="", max_length=64)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=20000)
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
