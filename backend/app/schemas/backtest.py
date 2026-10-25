"""回测请求 schema"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.ai_trading.strategies import QUANT_STRATEGY_TYPES
from app.schemas.instrument_scope import CnyFuturesSymbol


class BacktestRunRequest(BaseModel):
    """发起历史回测"""

    name: Annotated[str, Field(default="", max_length=120)]
    strategy_type: Annotated[
        str,
        Field(
            description=(
                "量化策略类型（以 ai_trading/strategies 注册表为准，"
                "见 /api/backtest/meta 的 strategies 列表）或 ai"
            ),
        ),
    ]
    strategy_params: Annotated[dict[str, Any] | None, Field(default=None)]
    model_row_id: Annotated[str | None, Field(default=None)]
    symbol: Annotated[CnyFuturesSymbol, Field(min_length=1, max_length=20)]
    symbol_name: Annotated[str, Field(default="", max_length=50)]
    timeframe: Annotated[str, Field(description="1m/5m/15m/30m/60m/1d")]
    start_date: Annotated[str, Field(description="YYYY-MM-DD")]
    end_date: Annotated[str, Field(description="YYYY-MM-DD")]
    side_mode: Annotated[str, Field(default="both")]
    fixed_qty: Annotated[int, Field(default=1, ge=1, le=100)]
    initial_cash: Annotated[float, Field(default=1_000_000, ge=10000)]
    # 与实盘 AI 任务对齐：资金仓=initial_cash；风格/提示词仅 AI 有效
    risk_style: Annotated[
        str,
        Field(
            default="balanced",
            description="aggressive/balanced/conservative",
        ),
    ]
    custom_prompt_enabled: Annotated[bool, Field(default=False)]
    custom_prompt: Annotated[str | None, Field(default=None, max_length=2000)]
    close_rules: Annotated[dict[str, Any] | None, Field(default=None)]
    stop_rules: Annotated[dict[str, Any] | None, Field(default=None)]
    # 多段回测：在 [start_date, end_date] 内随机抽取 segment_count 个
    # 互不重叠的标准段（每段 = 周期上限天数）分别独立回测后汇总。
    multi_segment: Annotated[bool, Field(default=False)]
    segment_count: Annotated[int, Field(default=2, ge=2, le=5)]

    @model_validator(mode="after")
    def multi_segment_ok(self) -> "BacktestRunRequest":
        if self.multi_segment and self.timeframe == "1d":
            raise ValueError("日线不支持多段回测，请切换分钟周期")
        return self

    @field_validator("strategy_type")
    @classmethod
    def strategy_ok(cls, value: str) -> str:
        v = value.strip().lower()
        # 白名单派生自策略注册表：新策略注册后自动放行（曾漏 strength 两档）
        allowed = ("ai", *QUANT_STRATEGY_TYPES)
        if v not in allowed:
            raise ValueError(f"strategy_type 须为 {' / '.join(allowed)}")
        return v

    @field_validator("timeframe")
    @classmethod
    def tf_ok(cls, value: str) -> str:
        if value not in ("1m", "5m", "15m", "30m", "60m", "1d"):
            raise ValueError("无效 timeframe")
        return value
