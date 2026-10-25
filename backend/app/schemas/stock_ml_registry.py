"""Read contracts for private experiment history; never authorizes a new evaluation."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from app.schemas.stock_research_v2 import Contract, DatasetRef, SHA256, aware_utc
from app.schemas.stock_ml import ModelManifest, TimeSplit, TrainingParameters


class ModelSummary(Contract):
    id: UUID
    dataset: DatasetRef
    state: Literal['ready','quarantined']
    validation_status: Literal['qa_only','research_only']
    created_at: datetime
    fold_count: int = Field(strict=True,ge=1,le=5)
    feature_names: list[str] = Field(min_length=1,max_length=16)
    holdout_registry_verified: Literal[False] = False
    _utc = field_validator('created_at')(aware_utc)


class ModelFold(Contract):
    fold_id: str = Field(pattern=SHA256)
    split: TimeSplit
    model: ModelManifest


class ModelDetail(ModelSummary):
    parameters: TrainingParameters
    folds: list[ModelFold] = Field(min_length=1,max_length=5)
    report_sha256: str = Field(pattern=SHA256)
    limitations: list[str] = Field(min_length=1,max_length=30)

    @model_validator(mode='after')
    def consistent(self):
        if self.fold_count != len(self.folds) or len(set(f.fold_id for f in self.folds)) != self.fold_count:
            raise ValueError('模型fold不一致')
        for fold in self.folds:
            if fold.model.dataset != self.dataset or fold.model.feature_order != self.feature_names or fold.model.parameters != self.parameters or fold.model.validation_status != self.validation_status or fold.model.training_knowledge_cutoff >= fold.split.test_start:
                raise ValueError('模型身份、参数或知识截止不一致')
        return self


class ModelPage(Contract):
    items: list[ModelSummary] = Field(max_length=100)
    total: int = Field(strict=True,ge=0)
    has_more: bool


class ModelReport(Contract):
    kind: Literal['stock-walk-forward-calculation-v1']
    report: dict
