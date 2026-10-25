"""AI 交易任务 ORM —— 自治模拟交易策略配置与状态"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AITradingTask(Base):
    """AI 交易任务表 —— 绑定模型/品种/周期与风控规则"""

    __tablename__ = "ai_trading_tasks"

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
    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="",
        comment="任务名称",
    )
    # AI 策略必填；量化规则策略可为 NULL
    model_row_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_models.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment="绑定的用户模型库记录 ID（量化策略可空）",
    )
    # ai = LLM；ma_cross = 双均线；n_breakout = N日突破
    strategy_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ai",
        index=True,
        comment="策略类型 ai/ma_cross/n_breakout",
    )
    strategy_params: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="策略参数 JSON（均线周期等）",
    )
    # 任务头像图标 slug（对应 /ai-icons/<slug>.png）；NULL=按模型/策略自动匹配
    icon: Mapped[str | None] = mapped_column(
        String(48),
        nullable=True,
        default=None,
        comment="任务头像图标 slug（ai-icons 资源名）",
    )
    symbol: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="合约代码",
    )
    symbol_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="",
        comment="合约名称",
    )
    timeframe: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="K 线周期 1m/5m/15m/30m/60m/1d",
    )
    # 多周期共振：主周期外的附加周期（去重去主周期后最多 3 个，总周期 ≤ 4；仅 AI 策略生效）
    extra_timeframes: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        comment="多周期共振附加周期列表（最多3个，仅 AI 策略生效）",
    )
    # AI 决策每个周期查看的 K 线根数（默认 40，10-240；仅 AI 策略生效）
    ai_bars_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=40,
        server_default="40",
        comment="AI 每周期查看 K 线根数（默认40，最大240）",
    )
    # 决策模型评估间隔秒（60-300，仅 decision 策略生效；Jev 低延迟按分钟级驱动）
    decision_interval_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default="60",
        comment="决策模型评估间隔秒（60-300，仅 decision 策略生效）",
    )
    # 量化任务快频评估间隔秒（全部量化策略生效，decision 用 decision_interval_sec）：
    # 0=按 K 线收盘对齐（默认，与回测口径一致）；
    # 60~K线周期秒数 → 按间隔评估，信号计算含实时未收盘 bar（盘中捕捉变化）
    quant_interval_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="量化任务快频评估间隔秒（0=按K线收盘对齐，60~K线周期秒数）",
    )
    # long_only / short_only / both
    side_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="both",
        comment="多空标准",
    )
    # full / half / fixed_qty / scale_in
    position_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="fixed_qty",
        comment="仓位模式 full/half/fixed_qty/scale_in",
    )
    fixed_qty: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="固定手数；滚仓时为每层单位手数（兼容字段，等同 qty_min）",
    )
    # 开仓手数范围 [qty_min, qty_max]
    qty_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="开仓最少手数",
    )
    qty_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="开仓最多手数",
    )
    # 资金仓使用比例范围（相对 allocated_capital）
    capital_usage_min_pct: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        comment="资金仓最低使用比例 0-100",
    )
    capital_usage_max_pct: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        nullable=False,
        default=Decimal("100"),
        server_default="100",
        comment="资金仓最高使用比例 0-100",
    )
    # 从用户模拟账户预留给本任务的 AI 资金仓额度（元）
    allocated_capital: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        comment="AI 资金仓额度（从用户可用资金预留）",
    )
    # 任务资金仓当前余额（per-task 独立子账本；reserve 时=allocated，开仓扣/平仓回/删除退）
    capital_balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        comment="任务资金仓当前余额（per-task 独立）",
    )
    # aggressive / balanced / conservative
    risk_style: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="balanced",
        server_default="balanced",
        comment="策略风格 aggressive/balanced/conservative",
    )
    # 默认按 K 线周期映射（1m=3/5m=7/15m=30/30m=30/60m=60/1d=90），用户可自行修改
    max_hold_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default="10",
        comment="最长持仓日历天数（交易周期）",
    )
    # 胜率统计：每次平仓后增量更新（按平仓笔数计，realized_pnl>0 为胜）
    win_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="盈利平仓笔数",
    )
    loss_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="亏损平仓笔数",
    )
    trade_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="累计平仓笔数",
    )
    win_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        comment="胜率百分比 win_count/trade_count*100",
    )
    # 用户自定义交易提示词（可选）
    custom_prompt_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否启用用户自定义提示词",
    )
    custom_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment="用户自定义交易提示词",
    )
    # 记录本任务品种最近一次开仓时间，用于最长持仓硬约束
    position_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="本任务品种当前持仓开仓时间",
    )
    close_rules: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="平仓规则 JSON",
    )
    stop_rules: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="止损规则 JSON",
    )
    # running / paused / stopped
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="paused",
        index=True,
        comment="任务状态",
    )
    # None / user / market_closed —— 区分手动暂停与休市自动暂停
    pause_reason: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        default=None,
        comment="暂停原因 user/market_closed",
    )
    equity_baseline: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0"),
        comment="启动时账户权益快照",
    )
    close_on_stop: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="结束任务时是否强制平该品种仓位",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="上次评估时间",
    )
    next_bar_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="下一根目标 K 线时间",
    )
    last_bar_time: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="已处理的最后一根 K 时间串，防重复",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="开始运行时间",
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="暂停时间",
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="结束时间",
    )
    # 累计实际运行秒数（仅 running 段累计，扣除手动/休市暂停段）
    runtime_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="累计实际运行秒数（扣除暂停）",
    )
    # 本次运行段起点（running 时有值；paused/stopped 时为 None）
    run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="本次运行段起点",
    )
    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="备注/最近错误摘要",
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
