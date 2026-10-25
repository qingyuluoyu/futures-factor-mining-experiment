"""模拟交易请求/响应 schema"""

from typing import Annotated
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.instrument_scope import CnyFuturesSymbol


class DepositFundsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[str, Field(strict=True, max_length=19, pattern=r"^(0|[1-9][0-9]{0,15})\.[0-9]{2}$")]
    idempotency_key: UUID

    @field_validator("amount")
    @classmethod
    def validate_positive_amount(cls, value: str) -> str:
        if Decimal(value) <= 0:
            raise ValueError("充值金额必须大于零，保留两位小数")
        return value


class PlaceOrderRequest(BaseModel):
    """模拟下单请求

    multiplier / margin_rate 可选；不传则使用品种默认 + 用户设置。
    """

    symbol: Annotated[CnyFuturesSymbol, Field(min_length=1, max_length=20, description="合约代码")]
    direction: Annotated[str, Field(description="buy / sell")]
    offset: Annotated[str, Field(description="open / close")]
    source: Annotated[
        str,
        Field(default="manual", description="来源 manual/ai/quant"),
    ]
    order_type: Annotated[str, Field(description="limit / market")]
    price: Annotated[
        float | None,
        Field(default=None, description="委托价；市价单传最新参考价"),
    ]
    quantity: Annotated[int, Field(ge=1, le=10000, description="手数")]
    symbol_name: Annotated[str, Field(default="", max_length=50, description="合约名称")]
    multiplier: Annotated[
        int | None,
        Field(default=None, description="合约乘数覆盖，默认按品种规格"),
    ]
    margin_rate: Annotated[
        float | None,
        Field(default=None, description="保证金率覆盖，默认按品种+用户设置"),
    ]


class UpdateTradeSettingsRequest(BaseModel):
    """更新用户模拟交易参数"""

    margin_scale: Annotated[
        float | None,
        Field(default=None, description="保证金率倍率，默认 1.0，范围 0.1~5"),
    ]
    fee_scale: Annotated[
        float | None,
        Field(default=None, description="手续费倍率，默认 1.0，范围 0~10"),
    ]
    margin_rate_override: Annotated[
        float | None,
        Field(default=None, description="全局保证金率覆盖 (0,1]"),
    ]
    fee_mode_override: Annotated[
        str | None,
        Field(default=None, description="手续费模式覆盖 rate/fixed"),
    ]
    open_fee_override: Annotated[
        float | None,
        Field(default=None, description="开仓手续费覆盖"),
    ]
    close_fee_override: Annotated[
        float | None,
        Field(default=None, description="平仓手续费覆盖"),
    ]
    clear_overrides: Annotated[
        bool,
        Field(default=False, description="true 时清除所有绝对覆盖项"),
    ]


class EstimateRequest(BaseModel):
    """预估保证金/手续费"""

    symbol: Annotated[CnyFuturesSymbol, Field(min_length=1, max_length=20)]
    price: Annotated[float, Field(gt=0)]
    quantity: Annotated[int, Field(ge=1, le=10000)]
    offset: Annotated[str, Field(default="open", description="open / close")]
