"""HTTP inputs and capability for existing private model history only."""
from typing import Literal

from pydantic import Field

from app.schemas.stock_research_v2 import Contract


class ModelListQuery(Contract):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class ModelReadQuery(Contract):
    pass


class ModelRegistryAvailability(Contract):
    listing_enabled: bool = Field(strict=True)
    training_enabled: Literal[False] = False
    reason: str = Field(min_length=1, max_length=500)
