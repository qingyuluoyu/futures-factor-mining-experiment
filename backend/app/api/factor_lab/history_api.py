"""因子历史 API"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factor_lab.common import champion_payload
from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.schemas.factor_lab import HistorySaveRequest
from app.services.factor_lab.persist.history_svc import (
    delete_history,
    list_history,
    upsert_champions_fifo,
)

logger = logging.getLogger("qihuo.factor_lab")
router = APIRouter()


@router.get("/history")
async def api_list_history(
    symbol: str = Query(..., min_length=1),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """当前用户某合约历史（≤10）"""
    items = await list_history(session, user.id, symbol)
    return {"symbol": symbol.strip().lower(), "items": items}


@router.post("/history")
async def api_save_history(
    body: HistorySaveRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """桌面端本地引擎搜索结果落历史（source=local）；同 tokens 去重，FIFO ≤10"""
    if not body.symbol.strip():
        raise HTTPException(status_code=422, detail="symbol 不能为空")
    if not 1 <= len(body.champions) <= 20:
        raise HTTPException(status_code=422, detail="champions 需为 1~20 条")
    payload = champion_payload([c.model_dump() for c in body.champions])
    try:
        # 试验登记（多重检验修正原料，best-effort，失败不影响保存主流程）
        if body.trials is not None and body.bars is not None:
            from app.services.factor_lab.persist.trial_svc import log_search_trial

            await log_search_trial(
                session,
                user_id=user.id,
                source="local",
                symbol=body.symbol,
                timeframe=body.timeframe,
                trials=body.trials,
                bars=body.bars,
                config=body.config,
                champions=payload,
            )
        saved = await upsert_champions_fifo(
            session,
            user.id,
            body.symbol,
            body.timeframe,
            payload,
            "local",
        )
        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        logger.exception("保存本地因子历史失败")
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}") from exc
    return {"ok": True, "saved": saved}


@router.delete("/history/{history_id}")
async def api_delete_history(
    history_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除一条历史"""
    ok = await delete_history(session, user.id, history_id)
    if not ok:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    await session.commit()
    return {"ok": True}
