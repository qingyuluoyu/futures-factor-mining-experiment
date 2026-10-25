from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.stock_research_dataset import StockResearchDataset
from app.models.user import User


class ColumnarBase(DeclarativeBase):
    pass


class StockColumnarDataset(ColumnarBase):
    __tablename__ = "stock_research_columnar_datasets"
    __table_args__ = (
        UniqueConstraint("owner_id", "trading_mode", "idempotency_key", name="uq_stock_columnar_scope_key"),
        CheckConstraint("trading_mode IN ('virtual','live')", name="ck_stock_columnar_mode"),
        CheckConstraint("state IN ('ready','quarantined')", name="ck_stock_columnar_state"),
        CheckConstraint("length(input_hash) = 64 AND length(manifest_sha256) = 64", name="ck_stock_columnar_hash"),
        Index("ix_stock_columnar_dataset_scope", "owner_id", "trading_mode", "dataset_id"),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchDataset.id, ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ColumnarSchemaVersion(ColumnarBase):
    __tablename__ = "stock_research_columnar_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
