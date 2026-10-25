"""策略收藏夹文件夹 REST API —— 因子/任务收藏共用，kind 区分"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.strategy_folder import StrategyFolder
from app.models.user import User

router = APIRouter(prefix="/api/strategy-folders", tags=["策略收藏夹"])

_VALID_KINDS = ("factor", "task")


class FolderCreate(BaseModel):
    kind: str = Field(description="factor=因子收藏 / task=任务收藏")
    name: str = Field(min_length=1, max_length=80)


class FolderRename(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _folder_to_dict(row: StrategyFolder) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "name": row.name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _get_user_folder(
    session: AsyncSession, folder_id: str, user_id: UUID
) -> StrategyFolder:
    try:
        uid = UUID(folder_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文件夹 ID")
    folder = (
        await session.execute(
            select(StrategyFolder).where(
                StrategyFolder.id == uid,
                StrategyFolder.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return folder


@router.get("")
async def list_folders(
    kind: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """文件夹列表（按创建时间升序）"""
    if kind not in _VALID_KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 factor 或 task")
    rows = (
        await session.execute(
            select(StrategyFolder)
            .where(
                StrategyFolder.user_id == user.id,
                StrategyFolder.kind == kind,
            )
            .order_by(StrategyFolder.created_at)
        )
    ).scalars().all()
    return {"items": [_folder_to_dict(r) for r in rows]}


@router.post("", status_code=201)
async def create_folder(
    body: FolderCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新建文件夹（同 kind 下名称唯一）"""
    if body.kind not in _VALID_KINDS:
        raise HTTPException(status_code=400, detail="kind 必须是 factor 或 task")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    dup = (
        await session.execute(
            select(StrategyFolder).where(
                StrategyFolder.user_id == user.id,
                StrategyFolder.kind == body.kind,
                StrategyFolder.name == name,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=400, detail="同名文件夹已存在")
    folder = StrategyFolder(user_id=user.id, kind=body.kind, name=name)
    session.add(folder)
    await session.commit()
    await session.refresh(folder)
    return _folder_to_dict(folder)


@router.patch("/{folder_id}")
async def rename_folder(
    folder_id: str,
    body: FolderRename,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """重命名文件夹"""
    folder = await _get_user_folder(session, folder_id, user.id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    dup = (
        await session.execute(
            select(StrategyFolder).where(
                StrategyFolder.user_id == user.id,
                StrategyFolder.kind == folder.kind,
                StrategyFolder.name == name,
                StrategyFolder.id != folder.id,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=400, detail="同名文件夹已存在")
    folder.name = name
    await session.commit()
    await session.refresh(folder)
    return _folder_to_dict(folder)


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除文件夹（其中收藏自动回到未分类，不删除收藏本身）"""
    folder = await _get_user_folder(session, folder_id, user.id)
    fid = folder.id
    await session.delete(folder)
    await session.commit()
    return {"deleted": True, "id": str(fid)}
