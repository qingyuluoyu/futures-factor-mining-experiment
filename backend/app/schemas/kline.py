"""K 线相关 Pydantic schema —— 请求/响应数据结构"""

from pydantic import BaseModel


class KlineBarSchema(BaseModel):
    """单根 K 线 bar

    settle / open_interest 在数据源不支持时为 None（前端展示 `--`）。
    """

    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    settle: float | None = None
    open_interest: int | None = None

    model_config = {"from_attributes": True}


class KlineRequestSchema(BaseModel):
    """K 线查询请求参数"""

    symbol: str
    period: str


class KlineResponseSchema(BaseModel):
    """K 线查询响应"""

    symbol: str
    period: str
    bars: list[KlineBarSchema]
    has_more: bool = False


class KlineBundlePeriodSchema(BaseModel):
    """bundle 响应中单个周期的数据段"""

    period: str
    bars: list[KlineBarSchema]
    has_more: bool = False


class KlineBundleResponseSchema(BaseModel):
    """K 线多周期打包响应（一次返回同合约全部分钟周期最新视图）"""

    symbol: str
    periods: list[KlineBundlePeriodSchema]
