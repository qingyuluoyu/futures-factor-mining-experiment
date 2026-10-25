"""AI Skill 模型 —— 已安装 skills 的元数据存储"""

import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Boolean, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AISkill(Base):
    """AI Skill 表 —— 存储用户安装的 skill 元数据"""

    __tablename__ = "ai_skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Skill 唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户 ID",
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Skill 名称",
    )
    trigger_word: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="触发词，用于 AI 工具调用时的名称",
    )
    source: Mapped[str] = mapped_column(
        SAEnum("marketplace", "upload", "git_url", name="ai_skill_source"),
        nullable=False,
        comment="来源：marketplace(商城)、upload(手动上传)、git_url(指令安装)",
    )
    source_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
        comment="来源 URL（商城 ID 或 GitHub 仓库地址）",
    )
    skill_md_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="本地 SKILL.md 文件路径",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="Skill 描述摘要",
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否启用（启用后 AI 可调用）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="安装时间",
    )


class AISkillConfig(Base):
    """AI Skill 配置表 —— 存储用户的 skills.sh token 等配置"""

    __tablename__ = "ai_skill_config"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="用户 ID（主键）",
    )
    skills_sh_token: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
        comment="skills.sh API token（AES-256-GCM 加密存储）",
    )
