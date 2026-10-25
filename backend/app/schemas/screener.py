"""行情筛选器 Pydantic schema —— 条件组合 + 参数可调"""

from typing import Any, Literal

from pydantic import BaseModel, Field

CONDITION_TYPES = ("macd", "ma", "kdj", "rsi", "boll")


class ScreenerCondition(BaseModel):
    """单个筛选条件。

    type 决定语义；各类型只读自己需要的字段，
    未传字段按类型默认值处理（见 services/market_screener.py）。
    """

    model_config = {"extra": "ignore"}

    type: Literal["macd", "ma", "kdj", "rsi", "boll"]
    op: str = ""
    window: int = Field(default=1, ge=1, le=50, description="在最近 N 根已收盘K内出现")

    # MACD
    fast: int = Field(default=12, ge=2, le=200)
    slow: int = Field(default=26, ge=2, le=200)
    signal: int = Field(default=9, ge=2, le=200)

    # 均线（排列三周期 / 穿越单周期）
    ma_short: int = Field(default=5, ge=2, le=300)
    ma_mid: int = Field(default=10, ge=2, le=300)
    ma_long: int = Field(default=20, ge=2, le=300)
    cross_period: int = Field(default=20, ge=2, le=300)

    # KDJ / RSI 共用字段（阈值默认按类型区分：KDJ 80/20，RSI 70/30）
    n: int = Field(default=9, ge=2, le=200)
    k_smooth: int = Field(default=3, ge=1, le=100)
    d_smooth: int = Field(default=3, ge=1, le=100)
    period: int = Field(default=14, ge=2, le=200)
    overbought: float | None = Field(default=None, ge=50, le=100)
    oversold: float | None = Field(default=None, ge=0, le=50)

    # 布林带
    std: float = Field(default=2.0, ge=0.5, le=5)


class ScreenerRequest(BaseModel):
    """POST /api/market/screener 请求体"""

    period: str = Field(default="1d", description="K线周期：1d / 60m / 30m / 15m / 5m")
    conditions: list[ScreenerCondition] = Field(
        default_factory=list, description="条件列表，全部满足才命中（组合筛选）"
    )


class ScreenerConditionResult(BaseModel):
    """单个条件的评估结果（用于结果明细展示）"""

    type: str
    label: str
    passed: bool
    detail: str = ""


class ScreenerItem(BaseModel):
    """命中品种"""

    symbol: str
    product_code: str = ""
    name: str = ""
    product_name: str = ""
    contract_name: str = ""
    period: str
    as_of: str = ""
    last_price: float | None = None
    direction: str = "neutral"
    conditions: list[ScreenerConditionResult] = Field(default_factory=list)


class ScreenerResponse(BaseModel):
    """筛选结果"""

    period: str
    period_label: str
    trading_mode: str
    scanned: int = 0
    total: int = 0
    items: list[ScreenerItem] = Field(default_factory=list)
    elapsed_ms: int = 0
    message: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
