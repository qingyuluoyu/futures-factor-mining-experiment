"""首页公开展示 API —— 无需登录"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.services.ai_trading.task import showcase_svc

router = APIRouter(prefix="/api/showcase", tags=["showcase"])


@router.get("/ai-trading")
async def showcase_ai_trading(
    request: Request,
    limit: int = Query(default=12, ge=1, le=24),
    trading_mode: str | None = Query(
        default=None,
        description="live / virtual / 空=全部优先 running",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """运行中 AI 任务收益对比 + 总收益柱（脱敏，供首页）"""
    redis_conn = getattr(request.app.state, "redis", None)
    mode = (trading_mode or "").strip().lower() or None
    if mode not in (None, "live", "virtual"):
        mode = None
    return await showcase_svc.list_showcase(
        session, redis_conn, limit, mode
    )
