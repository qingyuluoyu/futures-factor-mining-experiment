from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.stock_research_v2 import Contract, DatasetBuild, DatasetRef, SHA256

FORMAT_VERSION = "stock-source-parquet-v1"
ENGINE_VERSION = "26.0.0"


class ColumnarPartition(Contract):
    source_id: UUID
    source_sha256: str = Field(pattern=SHA256)
    data_version: str = Field(pattern=SHA256)
    kind: Literal["daily", "valuation", "financial"]
    sha256: str = Field(pattern=SHA256)
    relative_path: str = Field(pattern=r"^[a-f0-9]{64}\.parquet$")
    byte_size: int = Field(strict=True, gt=0, le=64 * 1024 * 1024)
    rows: int = Field(strict=True, ge=1, le=10000)

    @model_validator(mode="after")
    def path_matches(self):
        if self.relative_path != self.sha256 + ".parquet":
            raise ValueError("列式路径与内容指纹不一致")
        return self


class ColumnarManifest(Contract):
    format_version: Literal["stock-source-parquet-v1"] = FORMAT_VERSION
    engine_version: Literal["26.0.0"] = ENGINE_VERSION
    representation: Literal["source_observations_not_aligned"] = "source_observations_not_aligned"
    dataset: DatasetRef
    selection: DatasetBuild
    partitions: list[ColumnarPartition] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def binding(self):
        ids = [str(item.source_id) for item in self.partitions]
        if ids != sorted(set(ids)) or ids != sorted(str(item) for item in self.selection.source_ids) or any(item.data_version != self.dataset.data_version for item in self.partitions):
            raise ValueError("列式分区重复、无序或混用数据版本")
        if self.dataset.qualification != "observation_only" or sum(item.rows for item in self.partitions) > 1_000_000 or sum(item.byte_size for item in self.partitions) > 64 * 1024 * 1024:
            raise ValueError("列式归档资格或总预算无效")
        return self


class ColumnarRef(Contract):
    columnar_id: UUID
    dataset: DatasetRef
    manifest_sha256: str = Field(pattern=SHA256)
