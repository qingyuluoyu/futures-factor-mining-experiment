import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ArchiveCatalogQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(20, ge=1, le=20)
    offset: int = Field(0, ge=0, le=10000)
    trade_date: date | None = None
    snapshot_at: datetime | None = None
    membership_token: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def pinned_membership(self):
        if (self.snapshot_at is None) != (self.membership_token is None) or (self.offset > 0 and self.snapshot_at is None):
            raise ValueError("翻页必须同时携带原快照时间和目录成员标识")
        return self

    @field_validator("trade_date", mode="before")
    @classmethod
    def strict_date(cls, value):
        if value is not None and type(value) is not date and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)):
            raise ValueError("交易日必须为YYYY-MM-DD")
        return value

    @field_validator("snapshot_at", mode="before")
    @classmethod
    def timestamp_text(cls, value):
        if value is not None and not isinstance(value, datetime) and (not isinstance(value, str) or "T" not in value):
            raise ValueError("目录快照时间必须包含时区")
        return value

    @field_validator("snapshot_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("目录快照时间必须包含时区")
        return value
