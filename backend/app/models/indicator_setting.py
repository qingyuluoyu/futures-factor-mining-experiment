"""图表指标配置模型 —— 每用户一行，持久化 K线指标设置"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChartIndicatorSetting(Base):
    """用户图表指标配置 —— 每用户一行，config 存完整 IndicatorConfig JSON"""

    __tablename__ = "chart_indicator_settings"

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
        unique=True,
        comment="所属用户（唯一，每用户一行）",
    )
    config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="指标配置 JSON：maLines/macd/rsi/boll/jdk/strength",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
