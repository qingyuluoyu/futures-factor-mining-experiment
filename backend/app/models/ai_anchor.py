"""AI 看盘主播 ORM —— 主播任务配置与播报记录

主播任务：绑定 AI 模型/品种/分时K线段/根数/播放间隔/技术线组合，
由后台循环按间隔调用模型分析并播报结论（只播报，不下单）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _utc_now() -> datetime:
    """带时区的当前 UTC 时间（timestamptz 全链路保持 aware）。"""
    return datetime.now(timezone.utc)


from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIAnchorTask(Base):
    """AI 看盘主播任务表 —— 每用户每 trading_mode 仅一条（start 覆盖配置）"""

    __tablename__ = "ai_anchor_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="任务唯一标识",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    trading_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="live",
        server_default="live",
        index=True,
        comment="交易盘模式 live/virtual",
    )
    model_row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="主播使用的用户模型库记录 ID",
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="看盘品种代码",
    )
    # 分时K线段：1m/5m/15m/30m/60m
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="分时K线周期（= timeframes[0] 主时段冗余）",
    )
    # 分时K线段列表（最多3个，第一个为主时段）：["5m", "15m", "60m"]
    timeframes: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="分时K线段列表（最多3个，第一个为主时段）",
    )
    # 方向偏好：any=双向 / long=只做多 / short=只做空
    direction_mode: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="any",
        server_default="any",
        comment="方向偏好 any/long/short",
    )
    # 操盘策略：aggressive=激进 / balanced=稳健 / conservative=保守
    strategy: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="balanced",
        server_default="balanced",
        comment="操盘策略 aggressive/balanced/conservative",
    )
    # 交易风格：short=短线 / mid=中线 / long=长线（与操盘策略正交）
    horizon: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="short",
        server_default="short",
        comment="交易风格 short/mid/long",
    )
    bar_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        comment="分析K线根数（最新一根为起点往历史，30-100）",
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15,
        comment="播放间隔分钟数（5-30）",
    )
    # 技术线组合：[{"name": "MACD", "params": {"fast": 12, ...}}, ...]
    indicators: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        comment="选中的技术线及参数（可重复添加）",
    )
    # running / paused / stopped
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="stopped",
        server_default="stopped",
        index=True,
        comment="任务状态",
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="下次播报时间（闭盘顺延重查，不播报）",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="上次播报时间",
    )
    note: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="备注/最近评估异常摘要",
    )
    # 用户手动录入的持仓（持仓互动播报）：
    # {"symbol": "rb2610", "direction": "long"|"short", "price": 3200.0, "quantity": 5}
    # 为空时播报行为与未加此功能前完全一致
    manual_position: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="用户手动录入的持仓信息（可空）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )


class AIAnchorBroadcast(Base):
    """AI 看盘主播播报记录 —— 每次分析的结论留痕，同时作为后续分析的上下文"""

    __tablename__ = "ai_anchor_broadcasts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="播报唯一标识",
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_anchor_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属主播任务",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属用户",
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="播报品种",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="播报分时K线周期",
    )
    # long / short / neutral
    direction: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="neutral",
        comment="方向判断：做多/做空/中性",
    )
    entry: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="建议进场位置",
    )
    take_profit: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="建议止盈位置",
    )
    stop_loss: Mapped[float | None] = mapped_column(
        nullable=True,
        comment="建议止损位置",
    )
    # trade / wait
    action: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="wait",
        comment="做单还是观望",
    )
    commentary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="播报文案（技术看盘专家口吻）",
    )
    model_error: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否为模型调用失败的错误播报",
    )
    # 模型原始输出与指标摘要，仅审计与上下文重建
    model_output: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="模型原始 JSON 输出",
    )
    # 关键压力/支撑位：[{price, type: resistance|support, label}]
    key_levels: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="AI 标注的关键压力位/支撑位",
    )
    context_summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="本轮分析上下文摘要（指标快照等）",
    )
    bar_time: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="分析窗口最新一根K线时间",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        index=True,
        comment="播报时间",
    )
