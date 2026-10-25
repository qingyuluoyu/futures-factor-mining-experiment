from datetime import datetime
from uuid import UUID

from sqlalchemy import CHAR, JSON, CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class GpCheckpointBase(DeclarativeBase):
    pass


class GpCheckpointVersion(GpCheckpointBase):
    __tablename__ = "gp_checkpoint_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GpCheckpointSession(GpCheckpointBase):
    __tablename__ = "gp_checkpoint_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "market_type", "trading_mode", "idempotency_key", name="uq_gp_checkpoint_scope_key"),
        CheckConstraint("market_type = 'futures'", name="ck_gp_checkpoint_market"),
        CheckConstraint("trading_mode = 'live' OR trading_mode = 'virtual'", name="ck_gp_checkpoint_mode"),
        CheckConstraint("generation >= 0 AND generation <= 200 AND revision = generation", name="ck_gp_checkpoint_generation"),
        CheckConstraint("(generation = 0 AND checkpoint_json IS NULL AND checkpoint_hash IS NULL) OR (generation > 0 AND checkpoint_json IS NOT NULL AND checkpoint_hash IS NOT NULL)", name="ck_gp_checkpoint_state"),
    )
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    market_type: Mapped[str] = mapped_column(String(8), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    input_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=False)
    config_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    checkpoint_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True).with_variant(JSON(none_as_null=True), "sqlite"), nullable=True)
    checkpoint_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
