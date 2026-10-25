from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CHAR, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class RecoveryBase(DeclarativeBase):
    pass


class StockArchiveRecoveryVersion(RecoveryBase):
    __tablename__ = "stock_archive_recovery_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class StockArchiveRecovery(RecoveryBase):
    __tablename__ = "stock_archive_recoveries"
    __table_args__ = (
        UniqueConstraint("operator_id", "idempotency_key", name="uq_stock_archive_recovery_operator_key"),
        UniqueConstraint("source_job_id", "attempt_number", name="uq_stock_archive_recovery_source_attempt"),
        CheckConstraint("status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled'", name="ck_stock_archive_recovery_status"),
        CheckConstraint("(status = 'running' AND step = 'recovering') OR (status = 'completed' AND step = 'completed') OR (status = 'failed' AND step = 'failed') OR (status = 'cancelled' AND step = 'cancelled')", name="ck_stock_archive_recovery_step"),
        CheckConstraint("attempt_number >= 1 AND attempt_number <= 2", name="ck_stock_archive_recovery_attempt"),
        CheckConstraint("deadline_at > started_at AND updated_at >= started_at", name="ck_stock_archive_recovery_times"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_stock_archive_recovery_finished"),
        CheckConstraint("(status = 'running' AND finished_at IS NULL AND archive_batch_id IS NULL AND error_code IS NULL AND error_message IS NULL) OR (status = 'completed' AND finished_at IS NOT NULL AND archive_batch_id IS NOT NULL AND error_code IS NULL AND error_message IS NULL) OR ((status = 'failed' OR status = 'cancelled') AND finished_at IS NOT NULL AND archive_batch_id IS NULL AND error_code IS NOT NULL)", name="ck_stock_archive_recovery_result"),
        Index("ix_stock_archive_recovery_source_started", "source_job_id", "started_at"),
        Index("ix_stock_archive_recovery_status_deadline", "status", "deadline_at"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_job_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_data_version: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_batch_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
