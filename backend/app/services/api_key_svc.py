"""API Key 服务 —— 生成、哈希、认证、CRUD

key 明文仅在创建/重置时返回一次；DB 只存 SHA-256 哈希。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey

_KEY_PREFIX = "qihuo_"
# 明文前缀长度：存 key_prefix 用于列表掩码展示（前缀 + ****）
_PREFIX_LEN = 12


def _now() -> datetime:
    return datetime.utcnow()


def generate_raw_key() -> str:
    """生成新 key 明文：qihuo_<urlsafe token>"""
    return f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_key(raw: str) -> str:
    """SHA-256 哈希（仅存哈希，明文不可逆）"""
    return hashlib.sha256(raw.encode()).hexdigest()


def _prefix_of(raw: str) -> str:
    return raw[:_PREFIX_LEN]


async def create_api_key(
    session: AsyncSession, name: str
) -> tuple[ApiKey, str]:
    """创建 key，返回 (record, raw_key)。raw 仅此一次返回，调用方需提示保存。"""
    raw = generate_raw_key()
    record = ApiKey(
        name=(name or "").strip()[:120] or "未命名",
        key_prefix=_prefix_of(raw),
        key_hash=hash_key(raw),
        is_active=True,
    )
    session.add(record)
    await session.flush()
    return record, raw


async def authenticate(session: AsyncSession, raw: str) -> ApiKey | None:
    """按明文 key 认证：命中且 is_active 则更新 last_used_at 并返回。"""
    if not raw:
        return None
    stmt = select(ApiKey).where(
        ApiKey.key_hash == hash_key(raw),
        ApiKey.is_active.is_(True),
    )
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    record.last_used_at = _now()
    await session.flush()
    return record


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def get_api_key(session: AsyncSession, key_id: UUID) -> ApiKey | None:
    stmt = select(ApiKey).where(ApiKey.id == key_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def update_api_key(
    session: AsyncSession,
    key_id: UUID,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> ApiKey | None:
    record = await get_api_key(session, key_id)
    if record is None:
        return None
    if name is not None:
        record.name = (name or "").strip()[:120] or record.name
    if is_active is not None:
        record.is_active = is_active
    record.updated_at = _now()
    await session.flush()
    return record


async def rotate_api_key(
    session: AsyncSession, key_id: UUID
) -> tuple[ApiKey, str] | None:
    """重置 key：生成新明文+哈希，旧哈希立即失效。返回 (record, new_raw)。"""
    record = await get_api_key(session, key_id)
    if record is None:
        return None
    raw = generate_raw_key()
    record.key_prefix = _prefix_of(raw)
    record.key_hash = hash_key(raw)
    record.is_active = True
    record.last_used_at = None
    record.updated_at = _now()
    await session.flush()
    return record, raw


async def delete_api_key(session: AsyncSession, key_id: UUID) -> bool:
    record = await get_api_key(session, key_id)
    if record is None:
        return False
    await session.delete(record)
    await session.flush()
    return True
