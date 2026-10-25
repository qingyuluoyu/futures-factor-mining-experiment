"""AI 模型模型 —— SQLAlchemy ORM 定义"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIModel(Base):
    """AI 模型表 —— 存储用户的 AI 模型配置"""

    __tablename__ = "ai_models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="模型唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="所属用户 ID",
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属渠道 ID",
    )
    model_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="模型标识（如 gpt-4o、claude-sonnet-4）",
    )
    display_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="显示名称",
    )
    is_multimodal: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="是否支持多模态（图片输入）",
    )
    is_custom: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="是否为用户自定义模型",
    )
    capabilities: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment='能力标签列表，如 ["tool_calling", "vision", "thinking"]',
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
