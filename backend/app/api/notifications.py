"""站内通知 API —— 列表 / 未读数 / 已读 / 删除（按 JWT trading_mode 隔离）"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.schemas.notify import NotificationItem, UnreadCountResponse
from app.services.notify.notify_svc import (
    delete_notification,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)

router = APIRouter(prefix="/api/notifications", tags=["通知"])


@router.get("", response_model=list[NotificationItem])
async def list_notifications_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
    category: str = Query(default="", description="类别 system/risk/trade/fund，空=全部"),
    read_filter: str = Query(
        default="", alias="read", description="unread/read/空=全部"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[NotificationItem]:
    """分盘查询通知（按 created_at 倒序）"""
    items = await list_notifications(
        session,
        ctx.user.id,
        ctx.trading_mode,
        category=category,
        read_filter=read_filter,
        limit=limit,
        offset=offset,
    )
    return [NotificationItem.model_validate(it) for it in items]


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> UnreadCountResponse:
    """当前盘未读条数"""
    n = await unread_count(session, ctx.user.id, ctx.trading_mode)
    return UnreadCountResponse(unread=n)


@router.post("/{notify_id}/read")
async def mark_read_api(
    notify_id: UUID,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """标记单条已读"""
    ok = await mark_read(session, ctx.user.id, ctx.trading_mode, notify_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    return {"ok": True}


@router.post("/read-all")
async def mark_all_read_api(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """当前盘全部已读"""
    n = await mark_all_read(session, ctx.user.id, ctx.trading_mode)
    return {"updated": n}


@router.delete("/{notify_id}", status_code=status.HTTP_200_OK)
async def delete_notification_api(
    notify_id: UUID,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """删除单条通知"""
    ok = await delete_notification(session, ctx.user.id, ctx.trading_mode, notify_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    return {"ok": True}
