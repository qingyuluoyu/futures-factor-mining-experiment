"""因子收藏 API"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.schemas.factor_lab import (
    FavoriteCreateRequest,
    FavoritePatchRequest,
    ReorderRequest,
)
from app.services.factor_lab.persist.favorite_svc import (
    add_favorite,
    delete_favorite,
    list_favorites,
    patch_favorite,
)

router = APIRouter()


@router.get("/favorites")
async def api_list_favorites(
    symbol: str | None = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """收藏列表（可选 symbol 置顶同合约）"""
    items = await list_favorites(session, user.id, symbol)
    return {"items": items}


@router.post("/favorites")
async def api_add_favorite(
    body: FavoriteCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新增收藏（同 tokens 幂等）"""
    try:
        item = await add_favorite(
            session=session,
            user_id=user.id,
            tokens=body.tokens,
            text=body.text,
            name=body.name,
            symbol=body.symbol,
            timeframe=body.timeframe,
            composite=body.composite,
            metrics=body.metrics,
            note=body.note,
            folder_id=body.folder_id,
        )
        await session.commit()
        return item
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/favorites/{favorite_id}")
async def api_patch_favorite(
    favorite_id: UUID,
    body: FavoritePatchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新收藏名称/备注"""
    item = await patch_favorite(
        session, user.id, favorite_id, body.name, body.note, body.folder_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="收藏不存在")
    await session.commit()
    return item


@router.post("/favorites/reorder")
async def api_reorder_favorites(
    body: ReorderRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """拖拽排序：按传入 id 顺序持久化 sort_order"""
    from app.services.factor_lab.persist.favorite_svc import reorder_favorites

    await reorder_favorites(session, user.id, body.ids)
    await session.commit()
    return {"ok": True, "count": len(body.ids)}


@router.delete("/favorites/{favorite_id}")
async def api_delete_favorite(
    favorite_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """取消收藏"""
    ok = await delete_favorite(session, user.id, favorite_id)
    if not ok:
        raise HTTPException(status_code=404, detail="收藏不存在")
    await session.commit()
    return {"ok": True}
