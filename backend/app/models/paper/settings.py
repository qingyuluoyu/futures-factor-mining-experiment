"""用户模拟交易参数设置 —— 可覆盖默认保证金/手续费"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaperTradeSettings(Base):
    """用户交易参数：按 (user_id, trading_mode) 分盘"""

    __tablename__ = "paper_trade_settings"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "trading_mode",
            name="uq_paper_trade_settings_user_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="设置唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="用户 ID",
    )
    trading_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="live",
        server_default="live",
        index=True,
        comment="交易盘模式 live/virtual",
    )
    margin_scale: Mapped[Decimal] = mapped_column(
        Numeric(8, 4),
        nullable=False,
        default=Decimal("1.0"),
        comment="保证金率倍率",
    )
    fee_scale: Mapped[Decimal] = mapped_column(
        Numeric(8, 4),
        nullable=False,
        default=Decimal("1.0"),
        comment="手续费倍率",
    )
    margin_rate_override: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 4),
        nullable=True,
        comment="全局保证金率覆盖",
    )
    fee_mode_override: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        comment="手续费模式覆盖 rate/fixed",
    )
    open_fee_override: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8),
        nullable=True,
        comment="开仓手续费覆盖",
    )
    close_fee_override: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8),
        nullable=True,
        comment="平仓手续费覆盖",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )
