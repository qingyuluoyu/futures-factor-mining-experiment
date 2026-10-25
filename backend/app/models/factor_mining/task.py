"""超级因子挖掘任务 ORM —— 长程后台因子挖掘的状态机

仿 ai_trading_tasks 范式：DB 状态机 + scheduler 后台 loop 轮询推进。
区别：因子挖掘不绑定交易时段，任务创建后立即进入 running，由 loop 分代步进。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FactorMiningTask(Base):
    """超级因子挖掘任务表

    状态机：pending → running → completed / failed
           running ↔ paused（用户暂停/恢复）
           任意状态 → cancelled
    """

    __tablename__ = "factor_mining_tasks"

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
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
        comment="任务名称",
    )
    symbol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        comment="品种符号，如 rb888 / AU",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="1d",
        comment="K 线周期",
    )
    # ── 挖掘配置 ──────────────────────────────────────────────
    population: Mapped[int] = mapped_column(
        Integer, nullable=False, default=40, comment="种群大小"
    )
    generations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, comment="总代数"
    )
    max_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, comment="最大树深"
    )
    cost: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        comment="成本率，auto=按品种推导",
    )
    train_ratio: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.7,
        comment="训练/测试切分比例",
    )
    walk_forward_folds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, comment="walk-forward 折数"
    )
    live_fill_gate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="实盘口径门：测试段次根开盘成交 sortino>0 才晋级",
    )
    cross_validate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="跨品种验证：≥半数同板块伙伴品种 sortino>0 才晋级",
    )
    islands: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="岛模型岛数（1=关闭）",
    )
    seed_tokens: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="种子 token 列表（再进化用）"
    )
    device: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default="cpu",
        comment="计算设备 cpu/gpu（gpu 预留，本期未实现）",
    )
    # ── 数据范围（任务创建时记录） ────────────────────────────
    bars_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="加载数据根数"
    )
    data_range_from: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="数据起始时间"
    )
    data_range_to: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="数据结束时间"
    )
    # ── 状态机 ──────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
        comment="任务状态 pending/running/paused/completed/failed/cancelled",
    )
    pause_reason: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None, comment="暂停原因 user/error"
    )
    running_worker: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        comment="当前执行该任务的 worker 标识（防多 worker 重复执行）",
    )
    # ── 进度 ────────────────────────────────────────────────
    current_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="当前已完成的代数"
    )
    best_composite: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, comment="当前最优综合分"
    )
    progress_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="进度百分比 0-100"
    )
    # ── 结果 ────────────────────────────────────────────────
    champions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="完成后的冠军因子列表"
    )
    error_msg: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None, comment="失败时的错误摘要"
    )
    # ── 时间戳 ──────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        comment="任务创建时间",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始运行时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成/失败时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=datetime.utcnow,
        comment="最近更新时间",
    )

    def __repr__(self) -> str:
        return (
            f"<FactorMiningTask {self.id} {self.symbol} {self.timeframe} "
            f"{self.status} gen={self.current_generation}/{self.generations}>"
        )
