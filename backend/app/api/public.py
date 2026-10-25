"""对外公开数据 API —— X-API-Key 认证，供外部展示站对接

- GET /api/public/ai-trading：快照聚合（tasks + equity_series + profit_items
  + 各 total），支持 equity_from/equity_to 历史回填
- GET /api/public/ai-trading/decisions：全局分析记录流（keyset 增量）
- GET /api/public/ai-trading/trades：全局成交流（keyset 增量，含无决策成交）

现有 /api/showcase 保持公开给本站首页，本路由独立受 key 保护。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session, require_api_key
from app.services.ai_trading.task import public_stream_svc, showcase_svc

router = APIRouter(prefix="/api/public", tags=["对外公开"])


def _validate_cursor(since_ts: datetime | None, since_id: UUID | None) -> None:
    if (since_ts is None) != (since_id is None):
        raise HTTPException(
            status_code=400, detail="since_ts 与 since_id 必须同时提供"
        )


@router.get("/ai-trading")
async def public_ai_trading(
    request: Request,
    limit: int = Query(default=12, ge=1, le=24),
    trading_mode: str | None = Query(
        default=None,
        description="live / virtual / 空=全部优先 running",
    ),
    equity_from: datetime | None = Query(
        default=None, description="权益曲线区间起点（UTC，含）"
    ),
    equity_to: datetime | None = Query(
        default=None, description="权益曲线区间终点（UTC，不含），用于回填分页"
    ),
    _key: str = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """AI 交易展示数据（脱敏）

    展示站用 `X-API-Key` 头调用，返回与 /api/showcase/ai-trading 相同的结构
    （tasks / equity_series / profit_items / 各 total），供渲染收益对比图与
    总收益柱（不含任务列表）。不传 equity_from/equity_to 时 equity_series
    为每任务最近 400 点；传区间时每任务最多 2000 点（升序），取满 2000 点
    后把 equity_to 设为当页最早点的 ts 继续向更早翻页。
    """
    redis_conn = getattr(request.app.state, "redis", None)
    mode = (trading_mode or "").strip().lower() or None
    if mode not in (None, "live", "virtual"):
        mode = None
    return await showcase_svc.list_showcase(
        session, redis_conn, limit, mode,
        equity_from=equity_from, equity_to=equity_to,
    )


@router.get("/ai-trading/decisions")
async def public_ai_trading_decisions(
    since_ts: datetime | None = Query(default=None, description="游标时间"),
    since_id: UUID | None = Query(default=None, description="游标 id"),
    limit: int = Query(default=200, ge=1, le=200),
    _key: str = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """全局分析记录流（升序，跨全部任务，脱敏）

    keyset 增量：把上一页返回的 next_since_ts / next_since_id 原样回传，
    不重不漏。无自增序号，游标为 (created_at, id) 二元组。
    """
    _validate_cursor(since_ts, since_id)
    return await public_stream_svc.list_public_decisions(
        session, since_ts, since_id, limit
    )


@router.get("/ai-trading/trades")
async def public_ai_trading_trades(
    since_ts: datetime | None = Query(default=None, description="游标时间"),
    since_id: UUID | None = Query(default=None, description="游标 id"),
    limit: int = Query(default=200, ge=1, le=200),
    _key: str = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """全局成交流（升序，含强平/会话平等无决策成交）

    口径同内部 trade-marks：按委托 task_id 直查已成交单，附带手续费/
    保证金字段。任务删除后孤儿成交继续返回（task_name 为 null）。
    """
    _validate_cursor(since_ts, since_id)
    return await public_stream_svc.list_public_trades(
        session, since_ts, since_id, limit
    )
