"""AI 交易总收益 —— 一任务一柱（已实现 + 持仓浮盈）"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.ai_trading.decision import AITradingDecision
from app.models.ai_trading.task import AITradingTask
from app.models.paper.order import PaperOrder
from app.services.ai_trading.task.position_pnl import (
    get_task_position_pnl,
    source_for_task,
)

_QUANT_TYPES = (
    "ma_cross",
    "n_breakout",
    "macd_cross",
    "kdj_cross",
    "band_swing",
    "strength_entry",
    "strength_entry_v2",
)


def _default_icon(task: AITradingTask) -> str | None:
    """任务头像 slug：显式 icon 优先，否则按策略默认（ai=None 自动匹配）"""
    if task.icon:
        return str(task.icon)
    st = str(task.strategy_type or "").lower()
    if st == "factor":
        return "factor"
    if st in _QUANT_TYPES:
        return "quant"
    return None


async def _load_model_metas(
    session: AsyncSession,
    user_id: UUID,
    model_row_ids: list[UUID],
) -> dict[UUID, dict[str, Any]]:
    """批量加载模型元信息（model_id / display_name / provider_name）"""
    if not model_row_ids:
        return {}
    stmt = (
        select(AIModel, AIProvider)
        .join(AIProvider, AIModel.provider_id == AIProvider.id)
        .where(
            AIModel.id.in_(model_row_ids),
            AIModel.user_id == user_id,
        )
    )
    out: dict[UUID, dict[str, Any]] = {}
    for model, provider in (await session.execute(stmt)).all():
        out[model.id] = {
            "model_id": model.model_id,
            "display_name": model.display_name,
            "provider_name": provider.name,
        }
    return out


async def list_profit_close_bars(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    limit: int,
    redis_conn: object | None = None,
) -> dict[str, Any]:
    """按任务汇总总收益柱

    每条 bar 对应一个 AI 任务：
    - realized: 该任务关联平仓单已实现盈亏合计
    - unrealized: 该任务品种当前持仓浮盈
    - total_pnl: realized + unrealized
    """
    safe_limit = max(1, min(int(limit), 500))
    mode = str(trading_mode or "live")

    task_stmt = (
        select(AITradingTask)
        .where(
            AITradingTask.user_id == user_id,
            AITradingTask.trading_mode == mode,
        )
        .order_by(AITradingTask.created_at.desc())
        .limit(safe_limit)
    )
    tasks = list((await session.execute(task_stmt)).scalars().all())
    if not tasks:
        return {
            "total": 0,
            "total_realized": 0.0,
            "total_unrealized": 0.0,
            "total_pnl": 0.0,
            "open_position_count": 0,
            "open_positions": [],
            "items": [],
        }

    task_ids = [t.id for t in tasks]
    # 批量预加载模型元信息（柱图头像匹配用）
    model_row_ids = [t.model_row_id for t in tasks if t.model_row_id]
    model_metas = await _load_model_metas(session, user_id, model_row_ids)
    realized_map = await _realized_by_task(session, user_id, mode, task_ids)

    items: list[dict[str, Any]] = []
    open_details: list[dict[str, Any]] = []
    open_count = 0
    # 任务级隔离后各任务浮盈独立，直接累加（不再需要同品种去重）
    sum_realized = 0.0
    sum_unrealized = 0.0

    for task in tasks:
        tid = str(task.id)
        realized = float(realized_map.get(task.id, 0.0))
        meta = model_metas.get(task.model_row_id) if task.model_row_id else None
        sum_realized = round(sum_realized + realized, 2)

        pnl_info = await get_task_position_pnl(
            session, user_id, task.symbol, redis_conn,
            source_for_task(task), task_id=task.id,
        )
        qty = int(pnl_info.get("quantity") or 0)
        unrealized = float(pnl_info.get("unrealized_pnl") or 0) if qty > 0 else 0.0
        has_open = qty > 0
        if has_open:
            open_count += 1
            open_details.append(
                {
                    "task_id": tid,
                    "task_name": task.name,
                    "symbol": task.symbol,
                    "direction": pnl_info.get("direction"),
                    "quantity": qty,
                    "avg_price": pnl_info.get("avg_price"),
                    "last_price": pnl_info.get("last_price"),
                    "unrealized_pnl": unrealized,
                }
            )
            sum_unrealized = round(sum_unrealized + unrealized, 2)

        total_pnl = round(realized + unrealized, 2)
        items.append(
            {
                "id": tid,
                "task_id": tid,
                "task_name": task.name or task.symbol,
                "symbol": task.symbol,
                "symbol_name": task.symbol_name or task.symbol,
                "direction": str(pnl_info.get("direction") or ""),
                "offset": "task",
                "price": float(pnl_info.get("last_price") or 0),
                "filled_qty": qty,
                "fee": 0.0,
                "pnl": unrealized,
                "realized": round(realized, 2),
                "unrealized": round(unrealized, 2),
                "cumulative": total_pnl,
                "total_pnl": total_pnl,
                "filled_at": "",
                "decision_action": "task_total",
                "kind": "task",
                "status": task.status,
                # 模型展示字段（柱图头像匹配用）
                "model_row_id": str(task.model_row_id) if task.model_row_id else None,
                "strategy_type": task.strategy_type or "ai",
                "icon": _default_icon(task),
                "model_id": meta.get("model_id") if meta else None,
                "model_display_name": meta.get("display_name") if meta else None,
                "provider_name": meta.get("provider_name") if meta else None,
                "has_open_position": has_open,
            }
        )

    # 按总收益从高到低（盈利在上）
    items.sort(key=lambda x: float(x.get("total_pnl") or 0), reverse=True)
    total_pnl = round(sum_realized + sum_unrealized, 2)

    return {
        "total": len(items),
        "total_realized": round(sum_realized, 2),
        "total_unrealized": sum_unrealized,
        "total_pnl": total_pnl,
        "open_position_count": open_count,
        "open_positions": open_details,
        "items": items,
    }


async def _realized_by_task(
    session: AsyncSession,
    user_id: UUID,
    mode: str,
    task_ids: list[UUID],
) -> dict[UUID, float]:
    """各任务已实现平仓盈亏合计"""
    if not task_ids:
        return {}
    stmt = (
        select(
            AITradingDecision.task_id,
            func.coalesce(func.sum(PaperOrder.realized_pnl), 0),
        )
        .select_from(AITradingDecision)
        .join(PaperOrder, PaperOrder.id == AITradingDecision.order_id)
        .join(AITradingTask, AITradingTask.id == AITradingDecision.task_id)
        .where(
            AITradingDecision.user_id == user_id,
            AITradingTask.user_id == user_id,
            AITradingTask.trading_mode == mode,
            AITradingDecision.task_id.in_(task_ids),
            AITradingDecision.order_id.is_not(None),
            PaperOrder.offset == "close",
            PaperOrder.status == "filled",
        )
        .group_by(AITradingDecision.task_id)
    )
    rows = (await session.execute(stmt)).all()
    out: dict[UUID, float] = {}
    for task_id, total in rows:
        out[task_id] = float(total or 0)
    return out
