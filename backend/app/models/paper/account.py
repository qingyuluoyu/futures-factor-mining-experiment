"""模拟交易账户模型 —— 每位用户每个 trading_mode 一套账户"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaperAccount(Base):
    """模拟账户表 —— 按 (user_id, trading_mode) 隔离 live / virtual"""

    __tablename__ = "paper_accounts"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "trading_mode",
            name="uq_paper_accounts_user_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="账户唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    # live=实盘数据宇宙；virtual=虚拟盘（openctp 7×24）
    trading_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="live",
        server_default="live",
        index=True,
        comment="交易盘模式 live/virtual",
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="可用余额",
    )
    frozen_margin: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="冻结保证金",
    )
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="累计已实现盈亏",
    )
    total_claimed: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="累计领取金额",
    )
    last_claim_month: Mapped[str | None] = mapped_column(
        String(7),
        nullable=True,
        comment="上次领取月份 YYYY-MM",
    )
    currency: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="CNY",
        comment="币种",
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
