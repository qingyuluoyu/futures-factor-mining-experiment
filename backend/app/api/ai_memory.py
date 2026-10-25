"""AI 记忆管理 API —— 长期记忆和用户偏好的查看与删除"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.services.ai.long_term_memory import (
    list_all_memories,
    list_all_preferences,
    delete_memory,
    delete_preference,
)

router = APIRouter(prefix="/api/ai/memory", tags=["AI 记忆"])


@router.get("")
async def get_all_memory(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """获取用户的所有长期记忆和偏好"""
    user_id = str(current_user.id)
    memories = await list_all_memories(user_id)
    preferences = await list_all_preferences(user_id)
    return {"memories": memories, "preferences": preferences}


@router.delete("/memories/{memory_id}")
async def remove_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除指定长期记忆"""
    await delete_memory(memory_id)
    return {"ok": True}


@router.delete("/preferences/{pref_id}")
async def remove_preference(
    pref_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除指定用户偏好"""
    await delete_preference(pref_id)
    return {"ok": True}
