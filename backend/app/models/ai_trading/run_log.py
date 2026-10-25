"""AI/因子任务运行日志 ORM —— 信号级数据链（后续优化的原料）

与 AITradingDecision（决策审计）互补：run_log 记录任务生命周期内
每一次信号/下单/跳过/异常，detail JSONB 按 strategy_type 分层携带
复盘所需上下文（因子组合仓位快照、模型原始输出、账户状态等）。
写入为 best-effort，不阻塞交易主路径。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AITradingRunLog(Base):
    """任务运行日志（信号/下单/跳过/异常事件流）"""

    __tablename__ = "ai_trading_run_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="日志唯一标识",
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
        comment="触发 K 线时间",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8), nullable=False, default="", comment="周期"
    )
    symbol: Mapped[str] = mapped_column(
        String(20), nullable=False, default="", comment="合约"
    )
    strategy_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="", comment="策略类型 ai/factor/..."
    )
    side_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="both", comment="多空标准"
    )
    # info / warn / error
    level: Mapped[str] = mapped_column(
        String(8), nullable=False, default="info", index=True, comment="级别"
    )
    # signal / order / skip / error / session_close / hard_rule
    event: Mapped[str] = mapped_column(
        String(24), nullable=False, default="signal", index=True, comment="事件类型"
    )
    action: Mapped[str] = mapped_column(
        String(24), nullable=False, default="", comment="动作"
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True, comment="关联委托 ID"
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="原因（截 2000）"
    )
    detail: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="分层详情：因子快照/模型输出/账户状态/错误堆栈摘要",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="记录时间",
    )
