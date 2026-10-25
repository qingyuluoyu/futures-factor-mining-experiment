"""AI 交易决策日志 ORM —— 分析记录与关联委托"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AITradingDecision(Base):
    """每次评估/规则触发的决策审计"""

    __tablename__ = "ai_trading_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="决策唯一标识",
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_trading_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属任务",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    bar_time: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="触发的 K 线时间",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="",
        comment="周期",
    )
    # bar / stop / close_rule / manual / session_close
    trigger_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="bar",
        comment="触发类型",
    )
    context_summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="上下文摘要",
    )
    model_output: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="模型原始输出",
    )
    # hold / open_long / open_short / close / close_long / close_short / error
    action: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="hold",
        comment="执行动作",
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联模拟委托 ID",
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="原因说明",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="记录时间",
    )
