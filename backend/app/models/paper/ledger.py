"""模拟账户资金流水"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaperLedger(Base):
    """资金流水 —— 领取 / 冻结 / 解冻 / 手续费 / 开平仓结算"""

    __tablename__ = "paper_ledgers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="流水唯一标识",
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
    # claim / freeze / unfreeze / fee / open / close / settlement
    flow_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="流水类型",
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="变动金额（正入负出）",
    )
    balance_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="变动后可用余额",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="说明",
    )
    ref_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="关联对象类型 order/position",
    )
    ref_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="关联对象 ID",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="创建时间",
    )
