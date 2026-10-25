"""模拟委托单"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaperOrder(Base):
    """模拟委托 —— 市价即时成交，限价挂单待撤"""

    __tablename__ = "paper_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="委托唯一标识",
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="账户 ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="用户 ID",
    )
    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="合约代码",
    )
    symbol_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="",
        comment="合约名称",
    )
    # buy / sell
    direction: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="买卖方向 buy/sell",
    )
    # open / close
    offset: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="开平 open/close",
    )
    # 委托来源：manual/ai/quant（持仓按 source 独立聚合）
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="manual",
        server_default="manual",
        comment="来源 manual/ai/quant",
    )
    # 任务级隔离：AI/量化任务下单时关联到具体任务；手动单为 NULL
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        default=None,
        index=True,
        comment="关联的 AI 交易任务；NULL=手动下单",
    )
    # limit / market
    order_type: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        comment="委托类型 limit/market",
    )
    price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        comment="委托价（市价单为成交参考价）",
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="委托手数",
    )
    filled_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="已成交手数",
    )
    # pending / filled / cancelled / rejected
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
        comment="状态",
    )
    frozen_margin: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="本单冻结保证金",
    )
    fee: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="已收手续费",
    )
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="平仓实现盈亏",
    )
    multiplier: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        comment="合约乘数",
    )
    margin_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4),
        nullable=False,
        default=Decimal("0.10"),
        comment="保证金率",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="委托时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )
    filled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="成交时间",
    )
