"""模拟持仓"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaperPosition(Base):
    """模拟持仓 —— 任务级隔离（task_id）+ 手动单聚合（task_id IS NULL）

    AI/量化任务下单时 task_id 必填，每个任务独立持仓行；
    手动下单 task_id 为 NULL，按 (account, symbol, direction, source=manual) 聚合。
    唯一约束含 COALESCE(task_id) 由 main.py 裸 SQL DDL 管理（ORM 元数据只列基本列）。
    """

    __tablename__ = "paper_positions"

    __table_args__ = (
        # ORM 元数据约束（用于全新建表）；存量库的实际约束含 COALESCE，由 main.py DDL 维护
        UniqueConstraint(
            "account_id",
            "symbol",
            "direction",
            "source",
            "task_id",
            name="uq_paper_pos_account_symbol_dir_task",
        ),
        # 查询优化：按 task_id 查任务持仓
        Index("ix_paper_positions_task_id", "task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="持仓唯一标识",
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
    # long / short
    direction: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="持仓方向 long/short",
    )
    # 持仓来源：manual=手动下单 / ai=AI 任务 / quant=量化&因子
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="manual",
        server_default="manual",
        comment="持仓来源 manual/ai/quant",
    )
    # 任务级隔离：AI/量化任务下单时关联到具体任务；手动单为 NULL
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        default=None,
        comment="关联的 AI 交易任务；NULL=手动下单",
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="持仓手数",
    )
    available_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="可平手数",
    )
    avg_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        default=Decimal("0"),
        comment="开仓均价",
    )
    margin: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="占用保证金",
    )
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="本仓累计已实现盈亏",
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
        comment="首次开仓时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )
