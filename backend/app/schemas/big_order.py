"""大单预警 Pydantic schema —— 设置读写 + 命中记录回看"""

from datetime import date, datetime, timezone, timedelta
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

# 颜色：#RRGGBB，6 位十六进制
_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"

# 北京时区：occurred_at 序列化为北京时间字符串，避免前端取到 UTC 时分秒
_BJ_TZ = timezone(timedelta(hours=8))


class BigOrderSettingResponse(BaseModel):
    """大单预警设置响应"""

    trading_mode: str
    buy_enabled: bool
    buy_threshold: int
    buy_color: str
    sell_enabled: bool
    sell_threshold: int
    sell_color: str
    popup_enabled: bool
    sound_enabled: bool
    voice_enabled: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class BigOrderSettingUpdate(BaseModel):
    """更新大单预警设置（全部可选，部分更新）"""

    buy_enabled: bool | None = None
    buy_threshold: Annotated[int, Field(ge=1, le=100000)] | None = None
    buy_color: Annotated[str, Field(pattern=_HEX_COLOR)] | None = None
    sell_enabled: bool | None = None
    sell_threshold: Annotated[int, Field(ge=1, le=100000)] | None = None
    sell_color: Annotated[str, Field(pattern=_HEX_COLOR)] | None = None
    popup_enabled: bool | None = None
    sound_enabled: bool | None = None
    voice_enabled: bool | None = None


class TradeTickItem(BaseModel):
    """逐秒成交响应（全市场公共数据）"""

    id: UUID
    symbol: str
    direction: str
    volume: int
    price: float
    occurred_at: datetime
    trading_day: date

    model_config = {"from_attributes": True}

    @field_serializer("occurred_at")
    def _serialize_occurred_at_bj(self, value: datetime) -> str:
        """occurred_at 序列化为北京时间字符串，前端直接取时分秒即北京时间"""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_BJ_TZ).isoformat()


class VolumeProfileRow(BaseModel):
    """成交量分布单价格档：四路开平为估算（持仓差分比例分摊）"""

    price: float
    open_long: int
    close_long: int
    open_short: int
    close_short: int
    # 多单 = 开多 + 平空（主动买方力量）；空单 = 开空 + 平多（主动卖方力量）
    buy_total: int
    sell_total: int
    total: int


class VolumeProfileResponse(BaseModel):
    """成交量分布响应：当前交易日按价格聚合，价格档全量返回"""

    symbol: str
    trading_day: date
    asof: datetime | None = None
    # 最新一笔成交价（前端"最新价置顶"排序锚点）
    last_price: float | None = None
    rows: list[VolumeProfileRow]

    @field_serializer("asof")
    def _serialize_asof_bj(self, value: datetime | None) -> str | None:
        """asof 序列化为北京时间字符串（最新一笔成交时间）"""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(_BJ_TZ).isoformat()
