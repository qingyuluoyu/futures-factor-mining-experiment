from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator


class SetReportBookmark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bookmarked: StrictBool


class ReportBookmarkView(BaseModel):
    job_id: UUID
    market: Literal["stock"]
    bookmarked: bool
    created_at: datetime | None

    @model_validator(mode="after")
    def coherent_state(self):
        if self.bookmarked != (self.created_at is not None):
            raise ValueError("收藏状态与时间不一致")
        return self
