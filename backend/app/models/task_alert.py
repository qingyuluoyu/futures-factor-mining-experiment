"""任务预警模型 —— AI/量化任务下单与平仓的提醒设置

TaskAlertSetting：每用户每 trading_mode 一行，控制开仓/平仓预警与提醒方式。
与大单预警（big_order_alert_settings）分立：任务预警无阈值概念，事件即提醒。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskAlertSetting(Base):
    """任务预警设置 —— 任务下单/平仓事件的弹窗/提示音/语音开关"""

    __tablename__ = "task_alert_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="设置唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属用户",
    )
    trading_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="live",
        server_default="live",
        comment="交易盘模式 live/virtual",
    )
    # 开仓（任务下多/开空单）预警
    open_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="开仓预警开关",
    )
    # 平仓预警
    close_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="平仓预警开关",
    )
    # 提醒方式
    popup_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="右下角弹窗开关",
    )
    sound_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="提示音开关",
    )
    voice_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="语音播报开关",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="最近更新时间",
    )

    __table_args__ = (
        # 每用户每 trading_mode 仅一行设置
        Index(
            "ux_task_alert_settings_user_mode",
            "user_id",
            "trading_mode",
            unique=True,
        ),
    )
