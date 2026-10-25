from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CHAR, JSON, CheckConstraint, Date, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EodBase(DeclarativeBase):
    pass


class StockEodVersion(EodBase):
    __tablename__ = "stock_eod_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class StockEodBatch(EodBase):
    __tablename__ = "stock_eod_batches"
    __table_args__ = (
        CheckConstraint("status = 'running' OR status = 'completed' OR status = 'partially_completed' OR status = 'failed' OR status = 'no_data'", name="ck_stock_eod_status"),
        CheckConstraint("attempts = 1", name="ck_stock_eod_attempts"),
        CheckConstraint("(status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL)", name="ck_stock_eod_finished"),
        Index("ix_stock_eod_latest", "status", "trade_date"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    data_version: Mapped[str | None] = mapped_column(CHAR(64))
    snapshot_json: Mapped[dict] = mapped_column(JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
