from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.user import User


class CustomFactorBase(DeclarativeBase):
    pass


class StockCustomFactor(CustomFactorBase):
    __tablename__ = "stock_custom_factors"
    __table_args__ = (
        CheckConstraint("trading_mode IN ('virtual','live')", name="ck_stock_custom_factor_mode"),
        CheckConstraint("latest_revision >= 1", name="ck_stock_custom_factor_revision"),
        Index("ix_stock_custom_factor_scope", "owner_id", "trading_mode", "created_at", "id"),
    )
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), nullable=False)
    trading_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    latest_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockCustomFactorVersion(CustomFactorBase):
    __tablename__ = "stock_custom_factor_versions"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_stock_custom_version_revision"),
        CheckConstraint("direction IN ('high','low')", name="ck_stock_custom_version_direction"),
        CheckConstraint("lookback BETWEEN 0 AND 244", name="ck_stock_custom_version_lookback"),
    )
    factor_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockCustomFactor.id, ondelete="RESTRICT"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    expression_version: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(64), nullable=False)
    fields_json: Mapped[list] = mapped_column(JSON, nullable=False)
    lookback: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockCustomFactorMutation(CustomFactorBase):
    __tablename__ = "stock_custom_factor_mutations"
    __table_args__ = (CheckConstraint("trading_mode IN ('virtual','live')", name="ck_stock_custom_mutation_mode"),)
    owner_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(User.__table__.c.id, ondelete="RESTRICT"), primary_key=True)
    trading_mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    idempotency_key: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CustomFactorSchemaVersion(CustomFactorBase):
    __tablename__ = "stock_custom_factor_schema_versions"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
