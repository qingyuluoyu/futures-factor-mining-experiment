"""AI 任务收藏 REST API —— 收藏/取消/移动文件夹（配置快照）"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.models.ai_trading import TaskFavorite
from app.models.ai_trading.task import AITradingTask
from app.models.strategy_folder import StrategyFolder

router = APIRouter(prefix="/api/ai-trading/task-favorites", tags=["AI 任务收藏"])

TASK_FAVORITE_SOFT_LIMIT = 100


class TaskFavoriteCreate(BaseModel):
    task_id: str = Field(description="要收藏的任务 ID")
    folder_id: str | None = Field(default=None, description="目标文件夹（空=未分类）")


class TaskFavoriteMove(BaseModel):
    folder_id: str | None = Field(default=None, description="目标文件夹（空=未分类）")


class TaskFavoriteReorder(BaseModel):
    ids: list[str] = Field(description="按顺序的收藏 ID 列表")


def _jsonable(value: Any) -> Any:
    """JSONB 写入安全转换：Numeric 列读出的 Decimal、UUID、datetime 等
    递归转为基础类型（Decimal 直接进 JSON 会 500）"""
    from datetime import datetime as _dt
    from decimal import Decimal as _Decimal
    from uuid import UUID as _UUID

    if isinstance(value, _Decimal):
        return float(value)
    if isinstance(value, _UUID):
        return str(value)
    if isinstance(value, _dt):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _snapshot_of(task: AITradingTask) -> dict[str, Any]:
    """收藏时的配置快照 —— 克隆/创建优秀任务按它重建任务"""
    return _jsonable({
        "name": task.name,
        "icon": task.icon,
        "symbol": task.symbol,
        "symbol_name": task.symbol_name,
        "timeframe": task.timeframe,
        "strategy_type": task.strategy_type,
        "strategy_params": dict(task.strategy_params or {}),
        "model_row_id": str(task.model_row_id) if task.model_row_id else None,
        "side_mode": task.side_mode,
        "position_mode": task.position_mode,
        "fixed_qty": task.fixed_qty,
        "qty_min": task.qty_min,
        "qty_max": task.qty_max,
        "allocated_capital": float(task.allocated_capital or 0),
        "capital_usage_min_pct": task.capital_usage_min_pct,
        "capital_usage_max_pct": task.capital_usage_max_pct,
        "risk_style": task.risk_style,
        "custom_prompt_enabled": bool(task.custom_prompt_enabled),
        "custom_prompt": task.custom_prompt,
        "close_rules": dict(task.close_rules or {}),
        "stop_rules": dict(task.stop_rules or {}),
        "max_hold_days": task.max_hold_days,
        "close_on_stop": bool(task.close_on_stop),
        "status_at_save": task.status,
    })


def _fav_to_dict(
    row: TaskFavorite, task_data: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "folder_id": str(row.folder_id) if row.folder_id else None,
        "task_id": str(row.task_id) if row.task_id else None,
        "note": row.note,
        "snapshot": row.snapshot or {},
        "created_at": row.created_at.isoformat()
        if row.created_at
        else datetime.utcnow().isoformat(),
        # 活任务数据（任务被删后为 null，前端按快照降级展示）
        "task": task_data,
    }


async def _validated_folder(
    session: AsyncSession, user_id: UUID, folder_id: str | None
) -> UUID | None:
    if not folder_id:
        return None
    try:
        uid = UUID(folder_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文件夹 ID")
    folder = (
        await session.execute(
            select(StrategyFolder).where(
                StrategyFolder.id == uid,
                StrategyFolder.user_id == user_id,
                StrategyFolder.kind == "task",
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="任务收藏夹不存在")
    return folder.id


@router.get("")
async def list_task_favorites(
    request: Request,
    ctx=Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """任务收藏列表（合并活任务实时数据）"""
    from app.services.ai_trading.task.task_svc import get_task

    user_id = ctx.user.id
    redis_conn = getattr(request.app.state, "redis", None)
    rows = (
        await session.execute(
            select(TaskFavorite)
            .where(TaskFavorite.user_id == user_id)
            .order_by(
                TaskFavorite.sort_order.asc(),
                TaskFavorite.created_at.desc(),
            )
        )
    ).scalars().all()
    items = []
    for row in rows:
        task_data = None
        if row.task_id is not None:
            try:
                _, task_data = await get_task(
                    session, user_id, row.task_id, redis_conn, ctx.trading_mode
                )
            except HTTPException:
                task_data = None
        items.append(_fav_to_dict(row, task_data))
    return {"items": items}


@router.post("", status_code=201)
async def add_task_favorite(
    body: TaskFavoriteCreate,
    ctx=Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """收藏任务（重复收藏=移动到新文件夹并刷新快照）"""
    from app.services.ai_trading.task.task_svc import get_task

    user_id = ctx.user.id
    try:
        task_uid = UUID(body.task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的任务 ID")
    # 校验任务归属（get_task 内含 user + trading_mode 校验）
    task, task_data = await get_task(
        session, user_id, task_uid, None, ctx.trading_mode
    )
    folder_uid = await _validated_folder(session, user_id, body.folder_id)

    existing = (
        await session.execute(
            select(TaskFavorite).where(
                TaskFavorite.user_id == user_id,
                TaskFavorite.task_id == task_uid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.folder_id = folder_uid
        existing.snapshot = _snapshot_of(task)
        await session.commit()
        await session.refresh(existing)
        return _fav_to_dict(existing, task_data)

    count = int(
        (
            await session.execute(
                select(func.count()).select_from(TaskFavorite).where(
                    TaskFavorite.user_id == user_id
                )
            )
        ).scalar_one()
        or 0
    )
    if count >= TASK_FAVORITE_SOFT_LIMIT:
        raise HTTPException(status_code=400, detail="任务收藏已达上限")

    row = TaskFavorite(
        user_id=user_id,
        folder_id=folder_uid,
        task_id=task_uid,
        snapshot=_snapshot_of(task),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _fav_to_dict(row, task_data)


@router.patch("/{favorite_id}")
async def move_task_favorite(
    favorite_id: str,
    body: TaskFavoriteMove,
    ctx=Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """移动到文件夹（folder_id 空=未分类）"""
    user_id = ctx.user.id
    try:
        fid = UUID(favorite_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的收藏 ID")
    row = (
        await session.execute(
            select(TaskFavorite).where(
                TaskFavorite.id == fid, TaskFavorite.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="收藏不存在")
    row.folder_id = await _validated_folder(session, user_id, body.folder_id)
    await session.commit()
    await session.refresh(row)
    return _fav_to_dict(row, None)


@router.post("/reorder")
async def reorder_task_favorites(
    body: TaskFavoriteReorder,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """拖拽排序：按传入 id 顺序持久化 sort_order"""
    user_id = ctx.user.id
    ids = [x for x in body.ids if x]
    rows = (
        await session.execute(
            select(TaskFavorite).where(TaskFavorite.user_id == user_id)
        )
    ).scalars().all()
    by_id = {str(r.id): r for r in rows}
    for index, raw in enumerate(ids):
        row = by_id.get(str(raw))
        if row is not None:
            row.sort_order = index + 1
    await session.commit()
    return {"ok": True, "count": len(ids)}


@router.delete("/{favorite_id}")
async def delete_task_favorite(
    favorite_id: str,
    ctx=Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """取消收藏"""
    user_id = ctx.user.id
    try:
        fid = UUID(favorite_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的收藏 ID")
    row = (
        await session.execute(
            select(TaskFavorite).where(
                TaskFavorite.id == fid, TaskFavorite.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="收藏不存在")
    await session.delete(row)
    await session.commit()
    return {"deleted": True, "id": favorite_id}
