"""自选行情模型 —— 用户收藏的合约列表"""

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, UniqueConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Watchlist(Base):
    """自选行情表 —— 存储用户收藏的合约"""

    __tablename__ = "watchlist"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "trading_mode",
            "contract_symbol",
            name="uq_user_mode_contract",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="自选记录唯一标识",
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
    contract_symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="合约代码，如 rb2510；A股为6位代码",
    )
    market_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="futures",
        server_default="futures",
        comment="市场：futures=期货 / stock=A股（双市场隔离）",
    )
    contract_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="合约名称，如 螺纹钢2510",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="排序序号",
    )
    group_name: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="分组名称",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
