"""AI 对话模型 —— SQLAlchemy ORM 定义"""

import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIConversation(Base):
    """AI 对话表 —— 存储用户与 AI 的对话会话"""

    __tablename__ = "ai_conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="对话唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="所属用户 ID",
    )
    title: Mapped[str] = mapped_column(
        String(200),
        default="新对话",
        nullable=False,
        comment="对话标题（取首条消息前 20 字符自动生成）",
    )
    model_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
        comment="当前使用的模型标识",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        comment="最后更新时间",
    )


class AIMessage(Base):
    """AI 消息表 —— 存储对话中的每条消息"""

    __tablename__ = "ai_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="消息唯一标识",
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属对话 ID",
    )
    role: Mapped[str] = mapped_column(
        SAEnum("user", "assistant", "system", "tool", name="ai_message_role"),
        nullable=False,
        comment="消息角色",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="消息内容",
    )
    tool_calls: Mapped[list[dict] | dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="工具调用信息（JSON）",
    )
    tool_result: Mapped[list[dict] | dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="工具调用结果（JSON）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="创建时间",
    )
