"""站内通知模型 —— 成交 / 资金 / 风控 / 系统消息

按 (user_id, trading_mode) 隔离，与 paper/watchlist 一致。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 通知类别枚举（与前端 MessageCategory 对齐）
CATEGORY_SYSTEM = "system"
CATEGORY_RISK = "risk"
CATEGORY_TRADE = "trade"
CATEGORY_FUND = "fund"
NOTIFY_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_SYSTEM, CATEGORY_RISK, CATEGORY_TRADE, CATEGORY_FUND}
)


class Notification(Base):
    """站内通知表 —— 用户级、按盘隔离"""

    __tablename__ = "notifications"

    __table_args__ = (
        # 未读数查询高频：(user_id, trading_mode, read)
        Index(
            "ix_notifications_user_mode_read",
            "user_id",
            "trading_mode",
            "read",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="通知唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    trading_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="live",
        server_default="live",
        index=True,
        comment="交易盘模式 live/virtual",
    )
    category: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="类别 system/risk/trade/fund",
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="标题",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="正文",
    )
    read: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否已读",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
