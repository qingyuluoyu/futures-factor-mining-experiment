"""对外 API Key ORM —— 展示站对接凭证"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiKey(Base):
    """API Key 表 —— 管理后台生成，展示站用 X-API-Key 调 /api/public/*"""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键",
    )
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        comment="用途/展示站名称",
    )
    # 明文前 12 字符，列表掩码展示用（不存完整明文）
    key_prefix: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="明文前缀（掩码展示用）",
    )
    # SHA-256 哈希；认证时对请求 key 做同样哈希后比对
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 哈希（仅存哈希，明文不可逆）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="是否启用",
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="最后使用时间",
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
        comment="更新时间",
    )
