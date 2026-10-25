"""AI 渠道商模型 —— SQLAlchemy ORM 定义"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIProvider(Base):
    """AI 渠道商表 —— 存储用户配置的 AI 渠道信息"""

    __tablename__ = "ai_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="渠道唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="所属用户 ID",
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="渠道名称（如 OpenAI、Anthropic）",
    )
    api_type: Mapped[str] = mapped_column(
        SQLEnum("openai", "claude", "jev", name="api_type_enum"),
        nullable=False,
        comment="接口规范：openai / claude / jev（TypeSafe System One 决策模型）",
    )
    base_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="API 基础地址",
    )
    api_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="加密存储的 API Key",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="是否启用",
    )
    thinking_enabled: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        default=None,
        comment="思考模式：NULL=跟随模型自动，TRUE=强制开启，FALSE=强制关闭（仅 Claude 规范生效）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
