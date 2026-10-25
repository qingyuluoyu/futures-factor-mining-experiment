"""自选行情 REST API —— 增删改查端点（按 JWT trading_mode 分盘）"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistResponse,
    WatchlistSortRequest,
    WatchlistUpdate,
)
from app.services.watchlist import (
    add_to_watchlist,
    list_watchlist,
    merge_quotes_to_watchlist,
    remove_from_watchlist,
    update_group,
    update_sort_order,
)

router = APIRouter(prefix="/api/watchlist", tags=["自选行情"])


@router.post("", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
async def create_watchlist_item(
    body: WatchlistCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(get_auth_context),
) -> WatchlistResponse:
    """添加合约到自选列表"""
    try:
        item = await add_to_watchlist(
            session, ctx.user.id, body, ctx.trading_mode
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    redis_conn = request.app.state.redis
    merged = await merge_quotes_to_watchlist([item], redis_conn)
    return WatchlistResponse(**merged[0])


@router.get("", response_model=list[WatchlistResponse])
async def get_watchlist(
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(get_auth_context),
    market: str = Query("futures", description="市场：futures=期货 / stock=A股"),
) -> list[WatchlistResponse]:
    """获取当前用户当前盘的自选合约列表（含实时行情，按市场隔离）"""
    items = await list_watchlist(session, ctx.user.id, ctx.trading_mode, market)
    redis_conn = request.app.state.redis
    merged = await merge_quotes_to_watchlist(items, redis_conn)
    return [WatchlistResponse(**item) for item in merged]


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist_item(
    item_id: str,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(get_auth_context),
) -> None:
    """删除自选合约"""
    from uuid import UUID

    try:
        item_uuid = UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="无效的 ID 格式"
        )

    success = await remove_from_watchlist(
        session, ctx.user.id, item_uuid, ctx.trading_mode
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="自选记录不存在"
        )


@router.put("/sort", status_code=status.HTTP_200_OK)
async def sort_watchlist(
    body: WatchlistSortRequest,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, str]:
    """批量更新自选排序"""
    items = [{"id": item.id, "sort_order": item.sort_order} for item in body.items]
    await update_sort_order(session, ctx.user.id, items)
    return {"message": "排序更新成功"}


@router.put("/{item_id}", response_model=WatchlistResponse)
async def update_watchlist_item(
    item_id: str,
    body: WatchlistUpdate,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(get_auth_context),
) -> WatchlistResponse:
    """更新自选记录（修改分组）"""
    from uuid import UUID

    try:
        item_uuid = UUID(item_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="无效的 ID 格式"
        )

    item = await update_group(session, ctx.user.id, item_uuid, body.group_name)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="自选记录不存在"
        )
    return WatchlistResponse.model_validate(item)
