"""用户模型 —— SQLAlchemy ORM 定义"""

import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Boolean, Enum as SQLEnum, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    """用户表 —— 存储账户信息和认证数据"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="用户唯一标识",
    )
    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="用户名",
    )
    phone: Mapped[str | None] = mapped_column(
        String(20),
        unique=True,
        nullable=True,
        comment="手机号",
    )
    email: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
        comment="邮箱",
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="bcrypt 哈希密码",
    )
    avatar: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="头像 URL",
    )
    real_name_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="是否已实名认证",
    )
    role: Mapped[str] = mapped_column(
        SQLEnum("user", "admin", name="user_role_enum"),
        default="user",
        nullable=False,
        comment="角色：user 或 admin",
    )
    status: Mapped[str] = mapped_column(
        SQLEnum("active", "frozen", name="user_status_enum"),
        default="active",
        nullable=False,
        comment="状态：active 或 frozen",
    )
    # ----- per-user 配额覆盖（null=用 SystemSettings 全局默认）-----
    max_tasks_override: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="任务数上限覆盖；null=用全局 max_tasks_per_user",
    )
    factor_lab_daily_override: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="因子实验室每日次数覆盖；null=用全局",
    )
    backtest_daily_override: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="历史回测每日次数覆盖；null=用全局",
    )
    # ----- 双市场偏好：futures=期货 / stock=A股 -----
    market_type: Mapped[str] = mapped_column(
        String(16),
        default="futures",
        nullable=False,
        server_default="futures",
        comment="当前市场偏好：futures=期货 / stock=A股",
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
