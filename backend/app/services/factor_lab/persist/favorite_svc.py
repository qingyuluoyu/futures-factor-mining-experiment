"""因子收藏服务 —— 用户级配方收藏（交易下拉数据源）"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.factor_lab import FactorFavorite
from app.models.strategy_folder import StrategyFolder
from app.services.factor_lab.persist.tokens_codec import (
    normalize_tokens,
    tokens_key,
    truncate_text,
)

FAVORITE_SOFT_LIMIT = 50


async def _validated_folder(
    session: AsyncSession,
    user_id: uuid.UUID,
    folder_id: str | None,
) -> uuid.UUID | None:
    """校验文件夹归属（须为当前用户的 factor 类文件夹）；空返回 None"""
    if not folder_id:
        return None
    try:
        uid = uuid.UUID(folder_id)
    except ValueError:
        return None
    folder = (
        await session.execute(
            select(StrategyFolder).where(
                StrategyFolder.id == uid,
                StrategyFolder.user_id == user_id,
                StrategyFolder.kind == "factor",
            )
        )
    ).scalar_one_or_none()
    return folder.id if folder else None


def _now() -> datetime:
    return datetime.utcnow()


def _row_to_dict(row: FactorFavorite) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "name": row.name,
        "tokens": list(row.tokens or []),
        "text": row.text,
        "composite": float(row.composite) if row.composite is not None else None,
        "metrics": dict(row.metrics) if isinstance(row.metrics, dict) else None,
        "note": row.note,
        "health": dict(row.health) if isinstance(row.health, dict) else None,
        "folder_id": str(row.folder_id) if row.folder_id else None,
        "sort_order": int(row.sort_order or 0),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def add_favorite(
    session: AsyncSession,
    user_id: uuid.UUID,
    tokens: list[int],
    text: str,
    name: str | None,
    symbol: str | None,
    timeframe: str | None,
    composite: float | None,
    metrics: dict[str, Any] | None,
    note: str | None,
    folder_id: str | None = None,
) -> dict[str, Any]:
    """新增或返回已有收藏（同 tokens_key 去重）"""
    toks = normalize_tokens(tokens)
    if not toks:
        raise ValueError("因子 tokens 不能为空")
    key = tokens_key(toks)
    if len(key) > 512:
        key = key[:512]
    stmt = select(FactorFavorite).where(
        FactorFavorite.user_id == user_id,
        FactorFavorite.tokens_key == key,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        # 重复收藏：更新到指定文件夹（同 tokens 幂等）
        existing.folder_id = await _validated_folder(session, user_id, folder_id)
        await session.flush()
        return _row_to_dict(existing)

    count_stmt = select(func.count()).select_from(FactorFavorite).where(
        FactorFavorite.user_id == user_id
    )
    count = int((await session.execute(count_stmt)).scalar_one() or 0)
    if count >= FAVORITE_SOFT_LIMIT:
        raise ValueError(f"收藏已达上限 {FAVORITE_SOFT_LIMIT}")

    display = truncate_text((name or text or key).strip(), 80) or key[:80]
    expr = truncate_text(text or key, 512)
    row = FactorFavorite(
        user_id=user_id,
        folder_id=await _validated_folder(session, user_id, folder_id),
        symbol=(symbol or "").strip().lower() or None,
        timeframe=timeframe,
        name=display,
        tokens=toks,
        tokens_key=key,
        text=expr,
        composite=composite,
        metrics=metrics,
        note=truncate_text(note or "", 200) or None,
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return _row_to_dict(row)


async def list_favorites(
    session: AsyncSession,
    user_id: uuid.UUID,
    symbol: str | None,
) -> list[dict[str, Any]]:
    """列出收藏；symbol 非空时优先匹配该合约，其余仍返回（不过滤丢弃）

    下拉场景：传 symbol 时把同 symbol 的排前面，再跟全局收藏。
    """
    stmt = (
        select(FactorFavorite)
        .where(FactorFavorite.user_id == user_id)
        .order_by(
            FactorFavorite.sort_order.asc(),
            FactorFavorite.created_at.desc(),
        )
        .limit(FAVORITE_SOFT_LIMIT)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    items = [_row_to_dict(r) for r in rows]
    sym = (symbol or "").strip().lower()
    if not sym:
        return items
    matched = [x for x in items if (x.get("symbol") or "") == sym]
    others = [x for x in items if (x.get("symbol") or "") != sym]
    return matched + others


async def patch_favorite(
    session: AsyncSession,
    user_id: uuid.UUID,
    favorite_id: uuid.UUID,
    name: str | None,
    note: str | None,
    folder_id: str | None = None,
) -> dict[str, Any] | None:
    """更新名称/备注；不存在返回 None"""
    stmt = select(FactorFavorite).where(
        FactorFavorite.id == favorite_id,
        FactorFavorite.user_id == user_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    if name is not None:
        row.name = truncate_text(name, 80) or row.name
    if note is not None:
        row.note = truncate_text(note, 200) or None
    if folder_id is not None:
        # 显式移动文件夹（空串=移回未分类）
        row.folder_id = await _validated_folder(
            session, user_id, folder_id or None
        )
    await session.flush()
    return _row_to_dict(row)


async def delete_favorite(
    session: AsyncSession,
    user_id: uuid.UUID,
    favorite_id: uuid.UUID,
) -> bool:
    """删除收藏"""
    stmt = select(FactorFavorite).where(
        FactorFavorite.id == favorite_id,
        FactorFavorite.user_id == user_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    return True


async def reorder_favorites(
    session: AsyncSession,
    user_id: uuid.UUID,
    ids: list[str],
) -> bool:
    """按传入顺序写 sort_order（拖拽排序持久化）"""
    for index, raw in enumerate(ids):
        try:
            fid = uuid.UUID(str(raw))
        except ValueError:
            continue
        row = (
            await session.execute(
                select(FactorFavorite).where(
                    FactorFavorite.id == fid,
                    FactorFavorite.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            row.sort_order = index + 1
    await session.flush()
    return True
