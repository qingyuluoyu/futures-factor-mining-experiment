"""大单预警 API —— 设置读写 + 全市场逐秒成交历史查询

- 大单预警设置：按 JWT (user, trading_mode) 隔离
- trade_tick 查询：全市场公共数据，需 JWT 鉴权但不按 user 隔离

注：旧的 /records 端点（命中阈值入库）已移除，大单历史改用 trade_tick 全量存储。
big_order_records 表保留（无 Alembic 不自动 drop），但代码不再引用。
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.schemas.big_order import (
    BigOrderSettingResponse,
    BigOrderSettingUpdate,
    TradeTickItem,
    VolumeProfileResponse,
)
from app.services.notify.big_order_svc import (
    get_or_create_setting,
    update_setting,
)
from app.services.notify.trade_tick_svc import (
    list_ticks,
    today_trading_day,
    volume_profile_ticks,
)

router = APIRouter(prefix="/api/users/me/big-orders", tags=["大单预警"])


@router.get("/settings", response_model=BigOrderSettingResponse)
async def get_setting_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> BigOrderSettingResponse:
    """读取当前盘大单预警设置（不存在则建默认行）"""
    row = await get_or_create_setting(session, ctx.user.id, ctx.trading_mode)
    return BigOrderSettingResponse.model_validate(row)


@router.put("/settings", response_model=BigOrderSettingResponse)
async def update_setting_api(
    body: BigOrderSettingUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> BigOrderSettingResponse:
    """更新大单预警设置（部分更新）"""
    row = await update_setting(
        session, ctx.user.id, ctx.trading_mode, body.model_dump(exclude_unset=True)
    )
    return BigOrderSettingResponse.model_validate(row)


# ===== 全市场逐秒成交历史（大单回看权威数据源） =====


@router.get("/trade-ticks", response_model=list[TradeTickItem])
async def list_trade_ticks_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
    symbol: Annotated[str | None, Query(max_length=20)] = None,
    direction: Annotated[str | None, Query(pattern="^(buy|sell)$")] = None,
    min_volume: Annotated[int | None, Query(ge=1)] = None,
    trading_day: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> list[TradeTickItem]:
    """查询全市场逐秒成交（在某品种内按方向/手数/交易日过滤）。

    不传 trading_day 时默认查当前归属交易日。全市场公共数据，仅按 JWT 鉴权。
    """
    day = trading_day if trading_day is not None else today_trading_day()
    rows = await list_ticks(
        session,
        symbol=symbol,
        direction=direction,
        min_volume=min_volume,
        trading_day=day,
        limit=limit,
    )
    return [TradeTickItem.model_validate(r) for r in rows]


@router.get("/volume-profile", response_model=VolumeProfileResponse)
async def get_volume_profile_api(
    symbol: Annotated[str, Query(min_length=1, max_length=20)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> VolumeProfileResponse:
    """成交量分布：当前交易日（前夜 21:00 夜盘起）按价格聚合的多空力量。

    价格档全量返回，手数/比例阈值筛选由前端实时交互。
    四路开平为估算（单秒主动买卖量 + 持仓变化比例分摊）。
    """
    return await volume_profile_ticks(session, symbol)
