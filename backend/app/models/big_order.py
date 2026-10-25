"""大单预警模型 —— 用户阈值/颜色/开关 + 全市场逐秒成交

- BigOrderAlertSetting：每用户每 trading_mode 一行，控制多/空单阈值与提醒方式
- TradeTick：全市场逐秒成交增量，大单历史的权威数据源

注：BigOrderRecord（命中阈值入库）已移除，big_order_records 表保留但代码不再引用。
大单判定数据源为盘口 current_volume（未拆分的真实单笔增量）。
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

DIRECTION_BUY = "buy"
DIRECTION_SELL = "sell"
BIG_ORDER_DIRECTIONS: frozenset[str] = frozenset({DIRECTION_BUY, DIRECTION_SELL})


class BigOrderAlertSetting(Base):
    """大单预警设置 —— 用户自选多/空单阈值、高亮色与提醒方式"""

    __tablename__ = "big_order_alert_settings"

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
    # 多单（主动买）
    buy_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="是否对多单启用判定",
    )
    buy_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="多单大单阈值（手）",
    )
    buy_color: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="#ef4444",
        server_default="#ef4444",
        comment="多单高亮色（hex）",
    )
    # 空单（主动卖）
    sell_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="是否对空单启用判定",
    )
    sell_threshold: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        comment="空单大单阈值（手）",
    )
    sell_color: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="#22c55e",
        server_default="#22c55e",
        comment="空单高亮色（hex）",
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
            "ux_big_order_settings_user_mode",
            "user_id",
            "trading_mode",
            unique=True,
        ),
    )


class TradeTick(Base):
    """全市场逐秒成交增量 —— 大单历史的权威数据源

    scheduler 每秒处理每个交易中的品种时，若该秒有成交（current_volume>0），
    写入一行。全市场公共数据（不按 user 隔离）。每晚 20:30 清空上一交易日。
    大单历史 = 在某品种内按方向/手数查询过滤本表。
    """

    __tablename__ = "trade_tick"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="记录唯一标识",
    )
    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="合约代码，如 rb2610",
    )
    direction: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="",
        server_default="",
        comment="buy=主动买/多；sell=主动卖/空；空串表示方向不明",
    )
    volume: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="该秒成交量增量（current_volume，未拆分）",
    )
    price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        comment="该秒最新价",
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="成交时间（北京时间）",
    )
    trading_day: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="归属交易日（21:00→次日为同一交易日）",
    )
    # 四路开平拆分（估算）：按单秒主动买卖量 + 持仓变化比例分摊；方向不明或持仓缺失时为 NULL
    open_long: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="估算开多手数（主动买中开仓部分）",
    )
    close_long: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="估算平多手数（主动卖中平仓部分）",
    )
    open_short: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="估算开空手数（主动卖中开仓部分）",
    )
    close_short: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="估算平空手数（主动买中平仓部分）",
    )

    __table_args__ = (
        # 按交易日清理（20:30 清空）
        Index("ix_trade_tick_day", "trading_day"),
        # 按品种+交易日+时间查询（历史回看主路径）
        Index(
            "ix_trade_tick_symbol_day_time",
            "symbol",
            "trading_day",
            "occurred_at",
        ),
        # 按品种+交易日+手数筛选（大单查询）
        Index(
            "ix_trade_tick_symbol_day_volume",
            "symbol",
            "trading_day",
            "volume",
        ),
    )

