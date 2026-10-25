"""图表指标配置 Pydantic schema —— 对齐前端 types/indicator.ts 的 IndicatorConfig"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class MALineSchema(BaseModel):
    """均线配置"""

    period: Annotated[int, Field(ge=2, le=500, description="均线周期 2-500")]
    color: str


class MACDSchema(BaseModel):
    """MACD 配置"""

    enabled: bool
    fastPeriod: Annotated[int, Field(ge=2, le=200)]
    slowPeriod: Annotated[int, Field(ge=2, le=200)]
    signalPeriod: Annotated[int, Field(ge=2, le=200)]
    difColor: str
    deaColor: str
    histUpColor: str
    histDownColor: str


class RSISchema(BaseModel):
    """RSI 配置（超买超卖）"""

    enabled: bool
    period: Annotated[int, Field(ge=2, le=200)]
    overbought: Annotated[int, Field(ge=50, le=100)]
    oversold: Annotated[int, Field(ge=0, le=50)]
    lineColor: str
    overboughtColor: str
    oversoldColor: str


class BOLLSchema(BaseModel):
    """布林带配置"""

    enabled: bool
    period: Annotated[int, Field(ge=2, le=200)]
    std: Annotated[float, Field(ge=0.5, le=5)]
    upperColor: str
    middleColor: str
    lowerColor: str
    upperStyle: Literal["solid", "dashed"]
    middleStyle: Literal["solid", "dashed"]
    lowerStyle: Literal["solid", "dashed"]


class JDKSchema(BaseModel):
    """JDK（KDJ）配置"""

    enabled: bool
    rsvPeriod: Annotated[int, Field(ge=2, le=200)]
    kPeriod: Annotated[int, Field(ge=2, le=50)]
    dPeriod: Annotated[int, Field(ge=2, le=50)]
    kColor: str
    dColor: str
    jColor: str


class StrengthSchema(BaseModel):
    """强弱指标配置（0–100 归一化副图 + 三档进场，算法见 services/signal_strength.py）

    全部字段带默认值（含 enabled）：携带旧版未含 strength 的请求体
    （缓存中的旧前端 bundle）回落默认且默认关闭，不因 schema 收紧而拒绝。
    """

    enabled: bool = False
    period: Annotated[int, Field(ge=2, le=200)] = 14
    smooth: Annotated[int, Field(ge=1, le=50)] = 3
    smooth2: Annotated[int, Field(ge=1, le=50)] = 1
    trendMaPeriod: Annotated[int, Field(ge=2, le=200)] = 10
    swingThreshold: Annotated[float, Field(ge=0, le=100)] = 50.0
    reboundThreshold: Annotated[float, Field(ge=0, le=100)] = 50.0
    oversoldLevel: Annotated[float, Field(ge=0, le=50)] = 20.0
    reboundLookback: Annotated[int, Field(ge=1, le=100)] = 10
    deepLevel: Annotated[float, Field(ge=0, le=30)] = 5.0
    deepBars: Annotated[int, Field(ge=1, le=50)] = 3
    cooldown: Annotated[int, Field(ge=0, le=200)] = 8
    upColor: str = "#ef4444"
    downColor: str = "#22d3ee"
    midLineColor: str = "#a855f7"
    signalColor: str = "#ffffff"


class StrengthV2Schema(BaseModel):
    """强弱指标 V2 配置（形态档：衰竭/确认/中继，双向；算法见 services/signal_strength_v2.py）

    全部字段带默认值（含 enabled）：携带旧版未含 strengthV2 的请求体回落
    默认且默认关闭，不因 schema 收紧而拒绝。数值范围对齐前端 StrengthV2Config。
    """

    enabled: bool = False
    period: Annotated[int, Field(ge=2, le=200)] = 14
    smooth: Annotated[int, Field(ge=1, le=50)] = 3
    smooth2: Annotated[int, Field(ge=1, le=50)] = 2
    continuationBand: Annotated[int, Field(ge=1, le=25)] = 5
    exhaustWindow: Annotated[int, Field(ge=2, le=20)] = 4
    shrinkRatio: Annotated[float, Field(ge=0.1, le=1)] = 0.45
    flatEps: Annotated[float, Field(ge=0.1, le=20)] = 1.2
    # 段内相对位置容差：距段峰/段谷 ≤ 此值算顶部/底部附近（方案一重定义）
    zoneDrop: Annotated[float, Field(ge=1, le=50)] = 10.0
    priceBufferAtrMult: Annotated[float, Field(ge=0, le=5)] = 0.3
    atrPeriod: Annotated[int, Field(ge=2, le=200)] = 14
    cooldown: Annotated[int, Field(ge=0, le=200)] = 8
    upColor: str = "#ef4444"
    downColor: str = "#22d3ee"
    midLineColor: str = "#a855f7"
    longSignalColor: str = "#ef4444"
    shortSignalColor: str = "#22c55e"


class IndicatorConfigSchema(BaseModel):
    """完整指标配置（对齐前端 IndicatorConfig，PUT 整体替换）"""

    maLines: Annotated[list[MALineSchema], Field(min_length=0, max_length=5)]
    macd: MACDSchema
    rsi: RSISchema
    boll: BOLLSchema
    jdk: JDKSchema
    # 强弱指标：缺省回落默认（enabled=False），旧请求体不被拒绝
    strength: StrengthSchema = StrengthSchema()
    # 强弱 V2（形态档）：缺省回落默认（enabled=False），旧请求体不被拒绝
    strengthV2: StrengthV2Schema = StrengthV2Schema()
    # 强弱版本二选一：缺省 v1，存量用户行为不变
    strengthVersion: Literal["v1", "v2"] = "v1"
