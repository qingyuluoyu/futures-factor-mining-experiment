"""策略收藏夹文件夹 —— 因子/任务收藏的一级分组菜单"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StrategyFolder(Base):
    """策略收藏夹文件夹（kind 区分因子/任务两类收藏）"""

    __tablename__ = "strategy_folders"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "name", name="uq_folder_user_kind_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="文件夹 ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    kind: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        comment="收藏类型：factor=因子 / task=AI任务",
    )
    name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        comment="文件夹名称（如：黑色系因子）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
