from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.user import User


class DatasetBase(DeclarativeBase):
    pass


class StockResearchArtifact(DatasetBase):
    __tablename__ = "stock_research_artifacts"
    __table_args__ = (
        UniqueConstraint("owner_id", "trading_mode", "idempotency_key", name="uq_stock_artifact_scope_key"),
        CheckConstraint("trading_mode IN ('virtual','live')", name="ck_stock_artifact_mode"),
        CheckConstraint("state IN ('ready','quarantined')", name="ck_stock_artifact_state"),
        CheckConstraint("purpose IN ('source','dataset')", name="ck_stock_artifact_purpose"),
        CheckConstraint("byte_size BETWEEN 1 AND 67108864", name="ck_stock_artifact_size"),
        CheckConstraint("length(sha256) = 64 AND relative_path = sha256 || '.json'", name="ck_stock_artifact_path"),
        Index("ix_stock_artifact_scope", "owner_id", "trading_mode", "created_at", "id"),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str] = mapped_column(String(80), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockResearchSource(DatasetBase):
    __tablename__ = "stock_research_sources"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_stock_source_artifact"),
        CheckConstraint("kind IN ('daily','valuation','financial')", name="ck_stock_source_kind"),
        CheckConstraint("row_count BETWEEN 1 AND 10000", name="ck_stock_source_rows"),
        Index("ix_stock_source_stream", "stream_key", "created_at", "id"),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchArtifact.id, ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    stream_key: Mapped[str] = mapped_column(String(200), nullable=False)
    revises_id: Mapped[PythonUUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_research_sources.id", ondelete="RESTRICT"), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockResearchDataset(DatasetBase):
    __tablename__ = "stock_research_datasets"
    __table_args__ = (
        UniqueConstraint("manifest_artifact_id", name="uq_stock_dataset_manifest"),
        CheckConstraint("qualification = 'observation_only'", name="ck_stock_dataset_qualification"),
        CheckConstraint("length(data_version) = 64", name="ck_stock_dataset_version"),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    manifest_artifact_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchArtifact.id, ondelete="RESTRICT"), nullable=False)
    data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    qualification: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockResearchDatasetSource(DatasetBase):
    __tablename__ = "stock_research_dataset_sources"
    dataset_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchDataset.id, ondelete="RESTRICT"), primary_key=True)
    source_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchSource.id, ondelete="RESTRICT"), primary_key=True)


class DatasetSchemaVersion(DatasetBase):
    __tablename__ = "stock_research_dataset_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
