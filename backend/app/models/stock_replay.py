"""日线回放专属模型；结构只通过版本迁移建立。"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric,
    String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.user import User


class ReplayBase(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_type():
    return JSONB().with_variant(JSON(), "sqlite")


class StockReplayDataset(ReplayBase):
    __tablename__ = "stock_replay_datasets"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="ck_stock_replay_dataset_dates"),
        Index("ix_stock_replay_dataset_code_created", "code", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    bars_json: Mapped[list] = mapped_column(_json_type(), nullable=False)
    meta: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now(),
    )


class StockReplayAccount(ReplayBase):
    __tablename__ = "stock_replay_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "trading_mode", "create_key", name="uq_stock_replay_account_create"),
        Index("ix_stock_replay_account_user_mode_created", "user_id", "trading_mode", "created_at"),
        CheckConstraint("status IN ('active', 'completed')", name="ck_stock_replay_account_status"),
        CheckConstraint("trading_mode IN ('live', 'virtual')", name="ck_stock_replay_account_mode"),
        CheckConstraint("revision >= 0", name="ck_stock_replay_account_revision"),
        CheckConstraint("initial_cash > 0 AND cash >= 0 AND fees_paid >= 0", name="ck_stock_replay_account_cash"),
        CheckConstraint('start_date <= "current_date" AND "current_date" <= end_date', name="ck_stock_replay_account_dates"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), nullable=False,
    )
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0")
    fees_paid: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    datasets_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    positions_json: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list, server_default="[]")
    settings_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    create_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(40), nullable=False, default="stock-replay-v1", server_default="stock-replay-v1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now, server_default=func.now(),
    )


class StockReplayOrder(ReplayBase):
    __tablename__ = "stock_replay_orders"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_stock_replay_order_key"),
        Index("ix_stock_replay_order_account_status_created", "account_id", "status", "created_at"),
        CheckConstraint("direction IN ('buy', 'sell')", name="ck_stock_replay_order_direction"),
        CheckConstraint("order_type IN ('market', 'limit')", name="ck_stock_replay_order_type"),
        CheckConstraint("status IN ('pending', 'filled', 'rejected', 'cancelled')", name="ck_stock_replay_order_status"),
        CheckConstraint("quantity > 0 AND quantity % 100 = 0", name="ck_stock_replay_order_quantity"),
        CheckConstraint("fees >= 0 AND reserved_cash >= 0", name="ck_stock_replay_order_amounts"),
        CheckConstraint("(order_type = 'market' AND limit_price IS NULL) OR (order_type = 'limit' AND limit_price IS NOT NULL AND limit_price > 0)", name="ck_stock_replay_order_limit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_replay_accounts.id", ondelete="RESTRICT"), nullable=False,
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(6), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    submitted_on: Mapped[date] = mapped_column(Date, nullable=False)
    filled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    filled_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0")
    cash_delta: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0")
    reserved_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"), server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now, server_default=func.now(),
    )


class StockReplayStep(ReplayBase):
    __tablename__ = "stock_replay_steps"
    __table_args__ = (
        UniqueConstraint("account_id", "idempotency_key", name="uq_stock_replay_step_key"),
        UniqueConstraint("account_id", "from_revision", name="uq_stock_replay_step_revision"),
        CheckConstraint("kind IN ('advance', 'cancel')", name="ck_stock_replay_step_kind"),
        CheckConstraint("from_revision >= 0", name="ck_stock_replay_step_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stock_replay_accounts.id", ondelete="RESTRICT"), nullable=False,
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, server_default=func.now(),
    )
