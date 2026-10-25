"""行情相关 Pydantic schema —— 合约与报价数据结构"""

from pydantic import BaseModel, Field


class ContractSchema(BaseModel):
    """合约基础信息（从 Redis 缓存读取）"""

    symbol: str
    name: str
    exchange: str

    model_config = {"from_attributes": True}


class QuoteSchema(BaseModel):
    """实时行情报价（WebSocket 推送数据结构）"""

    symbol: str
    name: str
    exchange: str
    last_price: float
    change: float
    change_pct: float
    open_price: float
    high_price: float
    low_price: float
    pre_close: float
    bid_price: float
    ask_price: float
    bid_vol: int
    ask_vol: int
    volume: int
    position: int
    tick_time: str

    model_config = {"from_attributes": True}


class OrderBookLevelSchema(BaseModel):
    """盘口单档数据"""

    price: float
    volume: int


class OrderBookSchema(BaseModel):
    """五档盘口数据"""

    symbol: str
    asks: list[OrderBookLevelSchema]
    bids: list[OrderBookLevelSchema]
    source: str | None = None
    asof: float | None = None
    stale: bool = False
    stats: dict[str, dict[str, object]] = Field(default_factory=dict)


class TradeRecordSchema(BaseModel):
    """逐笔成交记录"""

    time: str
    price: float
    volume: int
    direction: str


class ContractSearchResponse(BaseModel):
    """合约搜索响应"""

    contracts: list[ContractSchema]
    total: int


class QuoteMessage(BaseModel):
    """WebSocket 行情推送消息格式"""

    type: str
    data: list[QuoteSchema]


class OrderBookMessage(BaseModel):
    """WebSocket 盘口推送消息格式"""

    type: str
    data: list[OrderBookSchema]


class TradesMessage(BaseModel):
    """WebSocket 成交推送消息格式"""

    type: str
    data: dict[str, list[TradeRecordSchema]]
