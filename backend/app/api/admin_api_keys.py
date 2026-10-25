"""管理后台 API Key CRUD —— 增删改查 / 启停 / 重置"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_session
from app.models.api_key import ApiKey
from app.models.user import User
from app.services import api_key_svc

router = APIRouter(prefix="/api/admin/api-keys", tags=["管理后台-API Key"])


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ApiKeyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z") if value else ""


def _to_dict(record: ApiKey, *, raw: str | None = None) -> dict:
    """序列化；raw 仅在创建/重置时传入（仅本次响应可见）"""
    return {
        "id": str(record.id),
        "name": record.name,
        "key_prefix": record.key_prefix,
        "key_preview": f"{record.key_prefix}****",
        "is_active": record.is_active,
        "last_used_at": _iso(record.last_used_at) or None,
        "created_at": _iso(record.created_at) or "",
        "updated_at": _iso(record.updated_at) or "",
        "raw_key": raw,
    }


@router.get("")
async def list_keys(
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    items = await api_key_svc.list_api_keys(session)
    return {"total": len(items), "items": [_to_dict(k) for k in items]}


@router.post("")
async def create_key(
    body: ApiKeyCreateRequest,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    record, raw = await api_key_svc.create_api_key(session, body.name)
    return _to_dict(record, raw=raw)


@router.patch("/{key_id}")
async def update_key(
    key_id: UUID,
    body: ApiKeyUpdateRequest,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    record = await api_key_svc.update_api_key(
        session,
        key_id,
        name=body.name,
        is_active=body.is_active,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return _to_dict(record)


@router.post("/{key_id}/rotate")
async def rotate_key(
    key_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    res = await api_key_svc.rotate_api_key(session, key_id)
    if res is None:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    record, raw = res
    return _to_dict(record, raw=raw)


@router.delete("/{key_id}")
async def delete_key(
    key_id: UUID,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ok = await api_key_svc.delete_api_key(session, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return {"deleted": True, "id": str(key_id)}
