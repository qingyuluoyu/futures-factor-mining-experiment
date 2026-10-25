"""AI 看盘主播请求/响应 Schema"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

# 分时K线段（不含 tick 与日线）
AnchorTimeframe = Literal["1m", "5m", "15m", "30m", "60m"]
# 方向偏好：any=双向 / long=只做多 / short=只做空
AnchorDirectionMode = Literal["any", "long", "short"]
# 操盘策略：aggressive=激进 / balanced=稳健 / conservative=保守
AnchorStrategy = Literal["aggressive", "balanced", "conservative"]
# 交易风格（持仓周期维度，与操盘策略正交）：short=短线 / mid=中线 / long=长线
AnchorHorizon = Literal["short", "mid", "long"]


class AnchorKeyLevel(BaseModel):
    """AI 标注的关键压力位/支撑位"""

    price: Annotated[float, Field(description="价位")]
    type: Annotated[Literal["resistance", "support"], Field(description="resistance=压力位 support=支撑位")]
    label: Annotated[str, Field(default="", max_length=60, description="简短说明，如 前高压力")]


class AnchorIndicatorIn(BaseModel):
    """单条技术线选择（可重复添加，如 MA20 + MA60 两条均线）"""

    name: Annotated[str, Field(description="指标名 MA/EMA/BOLL/MACD/RSI/KDJ")]
    params: Annotated[
        dict[str, Any],
        Field(default_factory=dict, description="指标参数，缺省用默认值"),
    ]


class AnchorStartIn(BaseModel):
    """启动主播的配置请求"""

    model_row_id: uuid.UUID = Field(description="用户模型库记录 ID")
    symbol: Annotated[str, Field(min_length=1, max_length=32, description="看盘品种代码")]
    timeframes: Annotated[
        list[AnchorTimeframe] | None,
        Field(default=None, min_length=1, max_length=3, description="分时K线段列表，最多3个，第一个为主时段"),
    ]
    timeframe: Annotated[
        AnchorTimeframe | None,
        Field(default=None, description="兼容旧客户端的单周期；与 timeframes 二选一"),
    ]
    direction_mode: Annotated[
        AnchorDirectionMode,
        Field(default="any", description="方向偏好：any 双向 / long 只做多 / short 只做空"),
    ]
    strategy: Annotated[
        AnchorStrategy,
        Field(default="balanced", description="操盘策略：aggressive 激进 / balanced 稳健 / conservative 保守"),
    ]
    horizon: Annotated[
        AnchorHorizon,
        Field(default="short", description="交易风格：short 短线 / mid 中线 / long 长线"),
    ]
    bar_count: Annotated[int, Field(ge=30, le=100, description="分析K线根数 30-100")]
    interval_minutes: Annotated[int, Field(ge=5, le=30, description="播放间隔分钟 5-30")]
    indicators: Annotated[
        list[AnchorIndicatorIn],
        Field(min_length=1, max_length=12, description="技术线组合，至少一条"),
    ]

    @model_validator(mode="after")
    def _require_timeframe(self) -> "AnchorStartIn":
        """timeframes 与 timeframe 至少给一个；仅给单周期时归一为数组。"""
        if not self.timeframes and not self.timeframe:
            raise ValueError("timeframes 与 timeframe 至少提供一个")
        return self

    @property
    def effective_timeframes(self) -> list[str]:
        """归一后的分时段列表（timeframes 优先，单周期兜底）。"""
        if self.timeframes:
            return list(self.timeframes)
        return [str(self.timeframe)]


class AnchorConfigPatchIn(BaseModel):
    """运行中热更新主播配置（不打断播放节奏，不改 next_run_at）"""

    model_row_id: uuid.UUID | None = Field(
        default=None, description="切换后的用户模型库记录 ID"
    )
    strategy: AnchorStrategy | None = Field(default=None, description="操盘策略")
    horizon: AnchorHorizon | None = Field(
        default=None, description="交易风格 short/mid/long"
    )


class AnchorPosition(BaseModel):
    """用户手动录入的持仓"""

    symbol: str = Field(description="持仓合约代码")
    direction: Literal["long", "short"] = Field(description="long=多 short=空")
    price: float = Field(description="开仓价")
    quantity: float = Field(description="手数")


class AnchorPositionIn(AnchorPosition):
    """持仓录入请求"""

    symbol: Annotated[str, Field(min_length=1, max_length=32, description="持仓合约代码")]
    price: Annotated[float, Field(gt=0, description="开仓价")]
    quantity: Annotated[float, Field(gt=0, le=100000, description="手数")]


class AnchorPositionAdvice(BaseModel):
    """持仓操作建议（模型输出，仅持仓播报时存在）"""

    action: Literal["hold", "add", "reduce", "close"] = Field(
        description="hold=持有 add=加仓 reduce=减仓 close=离场"
    )
    note: str = Field(default="", max_length=160, description="建议理由")


class AnchorTaskOut(BaseModel):
    """主播任务配置与状态"""

    id: uuid.UUID
    model_row_id: uuid.UUID
    symbol: str
    timeframe: str
    timeframes: list[str] = Field(default_factory=list, description="分时K线段列表，第一个为主时段")
    direction_mode: str = "any"
    strategy: str = "balanced"
    horizon: str = "short"
    bar_count: int
    interval_minutes: int
    indicators: list[dict[str, Any]]
    status: str
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    market_open: bool = Field(description="当前品种是否处于交易时段")
    manual_position: AnchorPosition | None = Field(
        default=None, description="用户手动录入的持仓（空=普通播报）"
    )


class AnchorBroadcastOut(BaseModel):
    """单条播报记录"""

    id: uuid.UUID
    symbol: str
    timeframe: str
    direction: str
    entry: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    action: str
    commentary: str
    model_error: bool
    key_levels: list[AnchorKeyLevel] | None = None
    position_advice: AnchorPositionAdvice | None = Field(
        default=None, description="持仓操作建议（仅持仓播报）"
    )
    bar_time: str | None = None
    created_at: datetime


class AnchorStatusOut(BaseModel):
    """主播状态总览：任务 + 最近播报（无任务时 task 为 null）"""

    task: AnchorTaskOut | None = None
    recent_broadcasts: list[AnchorBroadcastOut] = Field(default_factory=list)
