from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CHAR, JSON, CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ArchiveBase(DeclarativeBase):
    pass


class StockArchiveVersion(ArchiveBase):
    __tablename__ = "stock_archive_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class StockDailyArchiveBatch(ArchiveBase):
    __tablename__ = "stock_daily_archive_batches"
    __table_args__ = (
        UniqueConstraint("source_batch_id", "method_version", name="uq_stock_daily_archive_source_method"),
        UniqueConstraint("id", "trade_date", name="uq_stock_daily_archive_id_date"),
        CheckConstraint("status = 'completed'", name="ck_stock_daily_archive_status"),
        CheckConstraint("row_count >= 1 AND row_count <= 10000", name="ck_stock_daily_archive_rows"),
        CheckConstraint("expected_count >= 1 AND expected_count <= 10000", name="ck_stock_daily_archive_expected"),
        CheckConstraint("missing_count >= 0 AND missing_count <= 10000", name="ck_stock_daily_archive_missing"),
        CheckConstraint("unknown_count >= 0 AND unknown_count <= 10000", name="ck_stock_daily_archive_unknown"),
        CheckConstraint("row_count + missing_count = expected_count", name="ck_stock_daily_archive_coverage"),
        CheckConstraint("source_collected_at <= archived_at", name="ck_stock_daily_archive_times"),
        Index("ix_stock_daily_archive_date", "trade_date", "archived_at"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    version: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    source_batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    source_data_version: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    method_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_metadata: Mapped[dict] = mapped_column(JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)


class StockDailyObservation(ArchiveBase):
    __tablename__ = "stock_daily_observations"
    __table_args__ = (
        UniqueConstraint("batch_id", "code", name="uq_stock_daily_observation_batch_code"),
        ForeignKeyConstraint(["batch_id", "trade_date"], ["stock_daily_archive_batches.id", "stock_daily_archive_batches.trade_date"],
            name="fk_stock_daily_observation_batch_date", ondelete="RESTRICT"),
        CheckConstraint("currency = 'CNY' AND price_adjustment = 'none' AND volume_unit = 'lot_100_shares' AND amount_unit = 'CNY'", name="ck_stock_daily_units"),
        CheckConstraint("code ~ '^[0-9]{6}$' AND ((exchange = 'SSE' AND code ~ '^6' AND instrument_id = code || '.SH') OR (exchange = 'SZSE' AND code ~ '^[03]' AND instrument_id = code || '.SZ') OR (exchange = 'BSE' AND code ~ '^[489]' AND instrument_id = code || '.BJ'))", name="ck_stock_daily_identity"),
        *(CheckConstraint(f"{name} {'>=' if name in ('volume', 'amount') else '>'} 0 AND {name} < 100000000000000000000", name=f"ck_stock_daily_{name}")
            for name in ("open", "high", "low", "close", "pre_close", "volume", "amount")),
        CheckConstraint("low <= open AND low <= close AND open <= high AND close <= high", name="ck_stock_daily_ohlc"),
        Index("ix_stock_daily_observation_code_date", "code", "trade_date"),
    )
    batch_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    code: Mapped[str] = mapped_column(CHAR(6), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    price_adjustment: Mapped[str] = mapped_column(String(8), nullable=False)
    volume_unit: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_unit: Mapped[str] = mapped_column(String(8), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    pre_close: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
