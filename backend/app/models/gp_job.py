from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, JSON, CheckConstraint, DateTime, Double, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.gp_checkpoint import GpCheckpointSession


class GpJobBase(DeclarativeBase):
    pass


class GpJobVersion(GpJobBase):
    __tablename__ = "gp_job_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GpJob(GpJobBase):
    __tablename__ = "gp_checkpoint_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "trading_mode", "idempotency_key", name="uq_gp_job_scope_key"),
        UniqueConstraint("input_id", name="uq_gp_job_input"),
        Index("ix_gp_jobs_queue", "status", "queued_at", "id"),
        Index("ix_gp_jobs_owner_created", "user_id", "trading_mode", "created_at", "id"),
        Index("ix_gp_jobs_lease", "lease_token"),
        CheckConstraint("market_type = 'futures'", name="ck_gp_job_market"),
        CheckConstraint("trading_mode = 'live' OR trading_mode = 'virtual'", name="ck_gp_job_mode"),
        CheckConstraint("status = 'pending' OR status = 'running' OR status = 'paused' OR status = 'completed' OR status = 'failed' OR status = 'cancelled' OR status = 'expired'", name="ck_gp_job_status"),
        CheckConstraint("generation >= 0 AND generation <= total_generations AND total_generations >= 1 AND total_generations <= 200 AND attempts >= 0 AND runtime_seconds >= 0 AND runtime_seconds <= 1800", name="ck_gp_job_progress"),
        CheckConstraint("(lease_token IS NULL AND lease_until IS NULL AND generation_deadline IS NULL AND claimed_at IS NULL) OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND generation_deadline IS NOT NULL AND claimed_at IS NOT NULL)", name="ck_gp_job_lease"),
        CheckConstraint("status <> 'running' OR lease_token IS NOT NULL", name="ck_gp_job_running"),
        CheckConstraint("(status = 'completed' AND result_json IS NOT NULL AND result_hash IS NOT NULL) OR (status <> 'completed' AND result_json IS NULL AND result_hash IS NULL)", name="ck_gp_job_result"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    market_type: Mapped[str] = mapped_column(String(8), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    input_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey(GpCheckpointSession.id, ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    total_generations: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_seconds: Mapped[float] = mapped_column(Double, nullable=False)
    lease_token: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite"), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
