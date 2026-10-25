from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, Scope, DatasetRef, SHA256, explicit_date, aware_utc


class TimeSplit(Contract):
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    _dates = field_validator("train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end", mode="before")(explicit_date)

    @model_validator(mode="after")
    def ordered(self):
        if not self.train_start <= self.train_end < self.validation_start <= self.validation_end < self.test_start <= self.test_end:
            raise ValueError("训练、验证、测试须按日期严格分离")
        return self


class TrainingParameters(Contract):
    n_estimators: int = Field(default=100, strict=True, ge=1, le=500)
    max_depth: int = Field(default=5, strict=True, ge=1, le=8)
    num_leaves: int = Field(default=31, strict=True, ge=2, le=63)
    min_child_samples: int = Field(default=20, strict=True, ge=1, le=10000)
    learning_rate: float = Field(default=0.05, strict=True, gt=0, le=0.5)
    reg_alpha: float = Field(default=0., strict=True, ge=0, le=100)
    reg_lambda: float = Field(default=0., strict=True, ge=0, le=100)
    seed: int = Field(default=20261025, strict=True, ge=0, le=2147483647)
    threads: Literal[2] = 2
    max_seconds: int = Field(default=120, strict=True, ge=1, le=600)

    @model_validator(mode="after")
    def bounded_tree(self):
        if self.num_leaves > 2 ** self.max_depth:
            raise ValueError("叶子数不得超过深度对应上限")
        return self


class ModelManifest(Contract):
    method_version: Literal["stock-native-lambdarank-v1"] = "stock-native-lambdarank-v1"
    scope: Scope
    dataset: DatasetRef
    parameters: TrainingParameters
    runtime: dict[str, str] = Field(min_length=5, max_length=10)
    feature_order: list[str] = Field(min_length=1, max_length=16)
    feature_versions: dict[str, str] = Field(min_length=1, max_length=16)
    training_sha256: str = Field(pattern=SHA256)
    validation_sha256: str = Field(pattern=SHA256)
    model_sha256: str = Field(pattern=SHA256)
    training_knowledge_cutoff: date
    created_at: datetime
    trees_used: int = Field(ge=1, le=500)
    elapsed_seconds: float = Field(ge=0)
    feature_gain: dict[str, float]
    validation_status: Literal["qa_only", "research_only"]
    holdout_registry_verified: Literal[False] = False
    model_api_calls: Literal[0] = 0
    _utc = field_validator("created_at")(aware_utc)

    @model_validator(mode="after")
    def features(self):
        import math
        import re
        if len(set(self.feature_order)) != len(self.feature_order) or set(self.feature_order) != set(self.feature_versions) or set(self.feature_order) != set(self.feature_gain):
            raise ValueError("模型特征顺序/版本/重要性不一致")
        if any(not re.fullmatch(SHA256, value) for value in self.feature_versions.values()):
            raise ValueError("特征版本无效")
        if any(not isinstance(name, str) or not 1 <= len(name) <= 200 for name in self.feature_order):
            raise ValueError("特征名称无效")
        if not math.isfinite(self.elapsed_seconds) or any(not math.isfinite(value) or value < 0 for value in self.feature_gain.values()):
            raise ValueError("模型耗时或特征重要性非有限")
        return self
