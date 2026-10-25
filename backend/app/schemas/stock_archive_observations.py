from pydantic import BaseModel, ConfigDict, Field


class ArchiveObservationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_id: str = Field(pattern=r"^(?:6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[489][0-9]{5}\.BJ)$")
