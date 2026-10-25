from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.stock_research_dataset import StockResearchDataset
from app.models.user import User


class V2JobBase(DeclarativeBase):
    pass


class StockResearchV2Job(V2JobBase):
    __tablename__ = "stock_research_v2_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "trading_mode", "idempotency_key", name="uq_stock_v2_job_key"),
        CheckConstraint("trading_mode IN ('virtual','live')", name="ck_stock_v2_job_mode"),
        CheckConstraint("kind = 'dataset'", name="ck_stock_v2_job_kind"),
        CheckConstraint("status IN ('pending','running','completed','failed','cancelled','expired')", name="ck_stock_v2_job_status"),
        CheckConstraint("attempts BETWEEN 0 AND 1", name="ck_stock_v2_job_attempts"),
        CheckConstraint("max_runtime_seconds BETWEEN 30 AND 300", name="ck_stock_v2_job_runtime"),
        CheckConstraint("max_queue_seconds BETWEEN 30 AND 900", name="ck_stock_v2_job_queue"),
        CheckConstraint("length(request_hash) = 64 AND length(request_id) = 32", name="ck_stock_v2_job_identity"),
        CheckConstraint("(status = 'completed' AND result_dataset_id IS NOT NULL AND result_data_version IS NOT NULL AND result_qualification IS NOT NULL AND length(result_data_version) = 64 AND result_qualification = 'observation_only') OR (status <> 'completed' AND result_dataset_id IS NULL AND result_data_version IS NULL AND result_qualification IS NULL)", name="ck_stock_v2_job_result"),
        CheckConstraint("status <> 'running' OR (worker_token IS NOT NULL AND started_at IS NOT NULL AND deadline_at IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_stock_v2_job_running"),
        CheckConstraint("(status IN ('pending','running') AND finished_at IS NULL) OR (status IN ('completed','failed','cancelled','expired') AND finished_at IS NOT NULL)", name="ck_stock_v2_job_finished"),
        Index("ix_stock_v2_job_scope", "owner_id", "trading_mode", "created_at", "id"),
        Index("ix_stock_v2_job_queue", "status", "created_at", "id"),
        Index("ix_stock_v2_job_lease", "status", "lease_expires_at"),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    max_runtime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_queue_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_token: Mapped[PythonUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queue_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_dataset_id: Mapped[PythonUUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchDataset.id, ondelete="RESTRICT"), nullable=True)
    result_data_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_qualification: Mapped[str | None] = mapped_column(String(24), nullable=True)


class StockResearchV2Worker(V2JobBase):
    __tablename__ = "stock_research_v2_workers"
    __table_args__ = (CheckConstraint("name = 'stock-v2-dataset-job-v1'", name="ck_stock_v2_worker_name"),)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    busy_job_id: Mapped[PythonUUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchV2Job.id, ondelete="RESTRICT"), nullable=True)


class V2JobSchemaVersion(V2JobBase):
    __tablename__ = "stock_research_v2_job_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
