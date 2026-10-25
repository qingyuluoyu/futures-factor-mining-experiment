"""自选行情相关 Pydantic schema —— 请求/响应数据结构"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    """添加自选请求体"""

    contract_symbol: Annotated[str, Field(max_length=20, description="合约代码")]
    contract_name: Annotated[str, Field(max_length=50, description="合约名称")]
    market_type: Annotated[
        str,
        Field(default="futures", description="市场：futures=期货 / stock=A股"),
    ] = "futures"


class WatchlistUpdate(BaseModel):
    """更新自选请求体（修改分组）"""

    group_name: str | None = None


class WatchlistSortItem(BaseModel):
    """排序项"""

    id: UUID
    sort_order: int


class WatchlistSortRequest(BaseModel):
    """批量排序请求体"""

    items: list[WatchlistSortItem]


class WatchlistResponse(BaseModel):
    """自选记录响应（含可选实时行情字段）"""

    id: UUID
    contract_symbol: str
    contract_name: str
    sort_order: int
    group_name: str | None
    created_at: datetime
    # 实时行情字段（从 Redis 合并，可能为空）
    last_price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    volume: int | None = None

    model_config = {"from_attributes": True}
