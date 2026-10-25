from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FactorExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expression: str = Field(min_length=1, max_length=4096)


class CustomFactorCreate(FactorExpression):
    idempotency_key: UUID
    name: str = Field(min_length=1, max_length=80)
    direction: Literal["high", "low"]
    description: str = Field(default="", max_length=1000)


class CustomFactorEdit(CustomFactorCreate):
    expected_revision: int = Field(strict=True, ge=1, le=2147483646)


class CustomFactorDisable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    expected_revision: int = Field(strict=True, ge=1, le=2147483647)


class CompiledFactorView(BaseModel):
    expression: str
    expression_version: str = Field(pattern="^[0-9a-f]{64}$")
    formula_version: Literal["stock-expression-v1"]
    fields: list[str]
    lookback: int = Field(ge=0, le=244)


class CustomFactorView(CompiledFactorView):
    id: UUID
    revision: int = Field(ge=1)
    latest_revision: int = Field(ge=1)
    name: str
    direction: Literal["high", "low"]
    description: str
    disabled: bool
    created_at: str


class CustomFactorPage(BaseModel):
    items: list[CustomFactorView] = Field(max_length=100)
    total: int = Field(ge=0)
    has_more: bool
