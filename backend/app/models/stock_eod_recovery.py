from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import JSON


class EodRecoveryBase(DeclarativeBase):
    pass


class StockEodRecovery(EodRecoveryBase):
    __tablename__ = "stock_eod_recoveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_eod_recovery_key"),
        UniqueConstraint("source_batch_id", name="uq_eod_recovery_batch"),
        CheckConstraint("source_calls = 0", name="ck_eod_recovery_no_source"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    source_batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_data_version: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    before_row: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_calls: Mapped[int] = mapped_column(Integer, nullable=False)
