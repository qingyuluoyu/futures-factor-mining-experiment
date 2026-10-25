"""因子历史服务 —— 每合约 FIFO 最多 10 条，同 tokens 去重更新"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.factor_lab import FactorHistory
from app.services.factor_lab.persist.tokens_codec import (
    normalize_tokens,
    tokens_key,
    truncate_text,
)

HISTORY_CAP = 10


def _now() -> datetime:
    return datetime.utcnow()


def _row_to_dict(row: FactorHistory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "tokens": list(row.tokens or []),
        "text": row.text,
        "composite": float(row.composite or 0.0),
        "metrics": dict(row.metrics or {}),
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def upsert_champions_fifo(
    session: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
    timeframe: str,
    champions: list[dict[str, Any]],
    source: str,
) -> int:
    """将 champions 写入历史并执行 FIFO；返回写入/更新条数"""
    sym = (symbol or "").strip().lower()
    if not sym or not champions:
        return 0
    ranked = sorted(
        champions,
        key=lambda c: float(c.get("composite") or 0.0),
        reverse=True,
    )
    touched = 0
    now = _now()
    for item in ranked:
        toks = normalize_tokens(item.get("tokens"))
        if not toks:
            continue
        key = tokens_key(toks)
        if len(key) > 512:
            key = key[:512]
        text = truncate_text(str(item.get("text") or ""), 512)
        composite = float(item.get("composite") or 0.0)
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        stmt = select(FactorHistory).where(
            FactorHistory.user_id == user_id,
            FactorHistory.symbol == sym,
            FactorHistory.tokens_key == key,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.timeframe = timeframe
            existing.tokens = toks
            existing.text = text or existing.text
            existing.composite = composite
            existing.metrics = metrics
            existing.source = source
            existing.updated_at = now
        else:
            session.add(
                FactorHistory(
                    user_id=user_id,
                    symbol=sym,
                    timeframe=timeframe,
                    tokens=toks,
                    tokens_key=key,
                    text=text or key,
                    composite=composite,
                    metrics=metrics,
                    source=source,
                    created_at=now,
                    updated_at=now,
                )
            )
        touched += 1

    await session.flush()
    await _trim_fifo(session, user_id, sym)
    return touched


async def _trim_fifo(
    session: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
) -> None:
    """超出 cap 删除 updated_at 最旧的记录"""
    stmt = (
        select(FactorHistory)
        .where(
            FactorHistory.user_id == user_id,
            FactorHistory.symbol == symbol,
        )
        .order_by(FactorHistory.updated_at.desc(), FactorHistory.created_at.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if len(rows) <= HISTORY_CAP:
        return
    for old in rows[HISTORY_CAP:]:
        await session.delete(old)


async def list_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
) -> list[dict[str, Any]]:
    """列出某合约历史（≤10，新到旧）"""
    sym = (symbol or "").strip().lower()
    if not sym:
        return []
    stmt = (
        select(FactorHistory)
        .where(
            FactorHistory.user_id == user_id,
            FactorHistory.symbol == sym,
        )
        .order_by(FactorHistory.updated_at.desc(), FactorHistory.created_at.desc())
        .limit(HISTORY_CAP)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def delete_history(
    session: AsyncSession,
    user_id: uuid.UUID,
    history_id: uuid.UUID,
) -> bool:
    """删除一条历史；成功 True，不存在 False"""
    stmt = select(FactorHistory).where(
        FactorHistory.id == history_id,
        FactorHistory.user_id == user_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    return True
