"""持久研究任务模型，结构仅通过独立版本迁移建立。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CHAR, JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.user import User


class ResearchJobBase(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_type():
    return JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite")


class StockResearchSchemaVersion(ResearchJobBase):
    __tablename__ = "stock_research_schema_versions"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockResearchJob(ResearchJobBase):
    __tablename__ = "stock_research_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "trading_mode", "idempotency_key", name="uq_stock_research_job_key"),
        Index("ix_stock_research_job_user_mode_created", "user_id", "trading_mode", "created_at", "id"),
        Index("ix_stock_research_job_status_created", "status", "created_at"),
        Index("ix_stock_research_job_status_lease", "status", "lease_expires_at"),
        CheckConstraint("trading_mode = 'live' OR trading_mode = 'virtual'", name="ck_stock_research_job_mode"),
        CheckConstraint("kind = 'analysis' OR kind = 'backtest'", name="ck_stock_research_job_kind"),
        CheckConstraint("status = 'pending' OR status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled' OR status = 'expired'", name="ck_stock_research_job_status"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_stock_research_job_progress"),
        CheckConstraint("attempts >= 0 AND attempts <= 1", name="ck_stock_research_job_attempts"),
        CheckConstraint("result_bytes >= 0 AND result_bytes <= 8388608", name="ck_stock_research_job_result_bytes"),
        CheckConstraint("(status = 'completed' AND result_json IS NOT NULL AND data_version IS NOT NULL AND finished_at IS NOT NULL) OR (status <> 'completed' AND result_json IS NULL)", name="ck_stock_research_job_result"),
        CheckConstraint("status <> 'running' OR (worker_token IS NOT NULL AND started_at IS NOT NULL AND deadline_at IS NOT NULL AND lease_expires_at IS NOT NULL)", name="ck_stock_research_job_running"),
        CheckConstraint("status = 'pending' OR status = 'running' OR finished_at IS NOT NULL", name="ck_stock_research_job_finished"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    step: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", server_default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    params_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(40), nullable=False)
    data_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_manifest: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict, server_default="{}")
    result_json: Mapped[dict | None] = mapped_column(_json_type(), nullable=True)
    result_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    worker_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
