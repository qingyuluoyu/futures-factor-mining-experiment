"""Additive private ML history. This schema grants no holdout certification."""
from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.user import User
from app.models.stock_research_dataset import StockResearchDataset


class MLRegistryBase(DeclarativeBase):
    pass


class StockMLExperiment(MLRegistryBase):
    __tablename__ = 'stock_ml_experiments'
    __table_args__ = (
        UniqueConstraint('owner_id','trading_mode','idempotency_key',name='uq_stock_ml_experiment_scope_key'),
        CheckConstraint("trading_mode IN ('virtual','live')",name='ck_stock_ml_experiment_mode'),
        CheckConstraint("state IN ('ready','quarantined')",name='ck_stock_ml_experiment_state'),
        CheckConstraint("validation_status IN ('qa_only','research_only')",name='ck_stock_ml_experiment_validation'),
        CheckConstraint('fold_count BETWEEN 1 AND 5',name='ck_stock_ml_experiment_folds'),
        CheckConstraint('length(report_sha256) = 64 AND length(data_version) = 64',name='ck_stock_ml_experiment_hashes'),
        CheckConstraint('report_byte_size BETWEEN 1 AND 67108864',name='ck_stock_ml_experiment_report_size'),
        CheckConstraint('length(detail_json) BETWEEN 1 AND 524288',name='ck_stock_ml_experiment_detail_size'),
        Index('ix_stock_ml_experiment_scope','owner_id','trading_mode','created_at','id'),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True),primary_key=True)
    owner_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True),ForeignKey(User.__table__.c.id,ondelete='RESTRICT'),nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(16),nullable=False)
    idempotency_key: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True),nullable=False)
    request_id: Mapped[str] = mapped_column(String(32),nullable=False)
    dataset_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True),ForeignKey(StockResearchDataset.id,ondelete='RESTRICT'),nullable=False)
    data_version: Mapped[str] = mapped_column(String(64),nullable=False)
    report_sha256: Mapped[str] = mapped_column(String(64),nullable=False)
    report_byte_size: Mapped[int] = mapped_column(Integer,nullable=False)
    state: Mapped[str] = mapped_column(String(16),nullable=False)
    validation_status: Mapped[str] = mapped_column(String(24),nullable=False)
    fold_count: Mapped[int] = mapped_column(Integer,nullable=False)
    detail_json: Mapped[str] = mapped_column(Text,nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())


class MLRegistrySchemaVersion(MLRegistryBase):
    __tablename__ = 'stock_ml_registry_schema_versions'
    version: Mapped[str] = mapped_column(String(64),primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64),nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
