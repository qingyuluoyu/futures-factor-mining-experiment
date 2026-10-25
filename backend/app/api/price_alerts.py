"""价格预警 API —— CRUD（按 JWT trading_mode 隔离）"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.schemas.notify import PriceAlertCreate, PriceAlertItem, PriceAlertUpdate
from app.services.notify.price_alert_svc import (
    create_alert,
    delete_alert,
    list_alerts,
    update_alert_enabled,
)

router = APIRouter(prefix="/api/price-alerts", tags=["价格预警"])


@router.get("", response_model=list[PriceAlertItem])
async def list_alerts_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
    only_enabled: bool = False,
) -> list[PriceAlertItem]:
    """列出当前盘的价格预警（默认含已触发）"""
    items = await list_alerts(
        session, ctx.user.id, ctx.trading_mode, only_enabled=only_enabled
    )
    return [PriceAlertItem.model_validate(it) for it in items]


@router.post("", response_model=PriceAlertItem, status_code=status.HTTP_201_CREATED)
async def create_alert_api(
    body: PriceAlertCreate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> PriceAlertItem:
    """新建价格预警"""
    item = await create_alert(
        session,
        ctx.user.id,
        ctx.trading_mode,
        symbol=body.symbol,
        contract_name=body.contract_name,
        direction=body.direction,
        target_price=body.target_price,
    )
    return PriceAlertItem.model_validate(item)


@router.put("/{alert_id}", response_model=PriceAlertItem)
async def update_alert_api(
    alert_id: UUID,
    body: PriceAlertUpdate,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> PriceAlertItem:
    """启停预警（启用时清触发记录）"""
    item = await update_alert_enabled(
        session, ctx.user.id, ctx.trading_mode, alert_id, body.enabled
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")
    return PriceAlertItem.model_validate(item)


@router.delete("/{alert_id}", status_code=status.HTTP_200_OK)
async def delete_alert_api(
    alert_id: UUID,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """删除预警"""
    ok = await delete_alert(session, ctx.user.id, ctx.trading_mode, alert_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="预警不存在")
    return {"ok": True}
