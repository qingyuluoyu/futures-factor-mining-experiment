from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CHAR, CheckConstraint, Date, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ArchiveJobBase(DeclarativeBase):
    pass


class StockArchiveJobVersion(ArchiveJobBase):
    __tablename__ = "stock_archive_job_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class StockArchiveJob(ArchiveJobBase):
    __tablename__ = "stock_archive_jobs"
    __table_args__ = (
        UniqueConstraint("source_batch_id", "method_version", name="uq_stock_archive_job_source_method"),
        CheckConstraint("status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled'", name="ck_stock_archive_job_status"),
        CheckConstraint("(status = 'running' AND step = 'archiving') OR (status = 'completed' AND step = 'completed') OR (status = 'failed' AND step = 'failed') OR (status = 'cancelled' AND step = 'cancelled')", name="ck_stock_archive_job_step"),
        CheckConstraint("attempts = 1", name="ck_stock_archive_job_attempts"),
        CheckConstraint("deadline_at > started_at AND updated_at >= started_at", name="ck_stock_archive_job_times"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_stock_archive_job_finished"),
        CheckConstraint("(status = 'running' AND finished_at IS NULL AND archive_batch_id IS NULL AND error_code IS NULL AND error_message IS NULL) OR (status = 'completed' AND finished_at IS NOT NULL AND archive_batch_id IS NOT NULL AND error_code IS NULL AND error_message IS NULL) OR ((status = 'failed' OR status = 'cancelled') AND finished_at IS NOT NULL AND archive_batch_id IS NULL AND error_code IS NOT NULL)", name="ck_stock_archive_job_result"),
        Index("ix_stock_archive_job_status_deadline", "status", "deadline_at"),
        Index("ix_stock_archive_job_started", "started_at"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_data_version: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_batch_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
