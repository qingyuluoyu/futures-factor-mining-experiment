from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    market_type: Literal["futures", "stock"]
    trading_mode: Literal["live", "virtual"]
    request_id: UUID
