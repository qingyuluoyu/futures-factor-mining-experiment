"""AI 交易历史查询 —— 决策与关联委托"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.decision import AITradingDecision
from app.models.paper.order import PaperOrder
from app.services.ai_trading.task.task_svc import get_task, _iso
from app.services.paper.order_query import order_to_dict


async def list_decisions(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """分页决策记录"""
    await get_task(session, user_id, task_id)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)
    count_stmt = (
        select(func.count())
        .select_from(AITradingDecision)
        .where(
            AITradingDecision.task_id == task_id,
            AITradingDecision.user_id == user_id,
        )
    )
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    stmt = (
        select(AITradingDecision)
        .where(
            AITradingDecision.task_id == task_id,
            AITradingDecision.user_id == user_id,
        )
        .order_by(AITradingDecision.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    result = await session.execute(stmt)
    items = [
        {
            "id": str(d.id),
            "task_id": str(d.task_id),
            "bar_time": d.bar_time,
            "timeframe": d.timeframe,
            "trigger_type": d.trigger_type,
            "context_summary": d.context_summary,
            "model_output": d.model_output,
            "action": d.action,
            "order_id": str(d.order_id) if d.order_id else None,
            "reason": d.reason,
            "created_at": _iso(d.created_at) or "",
        }
        for d in result.scalars().all()
    ]
    return {"total": total, "items": items}


async def list_task_trades(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """通过 decision.order_id 关联 paper 委托"""
    await get_task(session, user_id, task_id)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    stmt = (
        select(AITradingDecision, PaperOrder)
        .join(PaperOrder, PaperOrder.id == AITradingDecision.order_id)
        .where(
            AITradingDecision.task_id == task_id,
            AITradingDecision.user_id == user_id,
            AITradingDecision.order_id.is_not(None),
        )
        .order_by(AITradingDecision.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    items = []
    for decision, order in rows:
        item = order_to_dict(order)
        item["decision_id"] = str(decision.id)
        item["decision_action"] = decision.action
        item["decision_reason"] = decision.reason
        items.append(item)
    return {"total": len(items), "items": items}


async def list_task_trade_marks(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    limit: int = 1000,
) -> dict[str, Any]:
    """任务 K 线交易标记 —— 按 task_id 直查已成交委托（升序，供前端画标记）。

    与 list_task_trades 的区别：不经 decision 关联（强平/会话平仓等无决策的
    成交也包含），只返回画标记所需最小字段。任务删除后本接口 404，标记随之消失。
    """
    await get_task(session, user_id, task_id)
    safe_limit = max(1, min(limit, 1000))

    stmt = (
        select(PaperOrder)
        .where(
            PaperOrder.task_id == task_id,
            PaperOrder.user_id == user_id,
            PaperOrder.status == "filled",
            PaperOrder.filled_qty > 0,
        )
        .order_by(PaperOrder.filled_at.asc())
        .limit(safe_limit)
    )
    result = await session.execute(stmt)
    items = [
        {
            "order_id": str(o.id),
            "task_id": str(task_id),
            "symbol": o.symbol,
            "direction": o.direction,
            "offset": o.offset,
            "price": float(o.price),
            "filled_qty": int(o.filled_qty),
            "time": _iso(o.filled_at or o.created_at) or "",
        }
        for o in result.scalars().all()
    ]
    return {"total": len(items), "items": items}
