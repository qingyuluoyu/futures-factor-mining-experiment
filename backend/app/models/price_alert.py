"""价格预警模型 —— 用户设定的价格触达提醒

触发后自动 enabled=False（避免刷屏），用户可重新启用。
按 (user_id, trading_mode) 隔离。
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

DIRECTION_ABOVE = "above"
DIRECTION_BELOW = "below"
ALERT_DIRECTIONS: frozenset[str] = frozenset({DIRECTION_ABOVE, DIRECTION_BELOW})


class PriceAlert(Base):
    """价格预警表 —— 最新价上穿/下穿目标价即提醒"""

    __tablename__ = "price_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="预警唯一标识",
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
    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="合约代码，如 rb2610",
    )
    contract_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="合约名称",
    )
    direction: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="above=涨到目标价；below=跌到目标价",
    )
    target_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="目标价",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
        comment="是否启用（触发后自动置 False）",
    )
    triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最近触发时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
