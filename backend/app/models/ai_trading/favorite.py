"""AI 任务收藏 ORM —— 收藏跑得好的实战任务（带配置快照）"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskFavorite(Base):
    """用户收藏的 AI 交易任务

    task_id 指向活任务（详情/历史/决策/修改/克隆都依赖任务存在）；
    任务被删除时置空，卡片按 snapshot 降级为只读展示。
    snapshot 保存收藏时的完整创建参数，克隆与“创建优秀任务”按它重建。
    """

    __tablename__ = "task_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "task_id", name="uq_task_favorite_user_task"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="收藏 ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("strategy_folders.id", ondelete="SET NULL"),
        nullable=True,
        comment="所属文件夹（空=未分类）",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_trading_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联任务（任务删除后置空，仅留快照）",
    )
    snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="收藏时的任务配置快照（创建参数 + 展示信息）",
    )
    note: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="收藏备注",
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="手动排序（小在前，默认 0 按收藏时间倒序）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="收藏时间",
    )
