"""对外公开只读流 —— 全局决策与成交（keyset 游标，供展示站增量拉取）

主键均为 UUID（无自增序号），游标用 (时间, id) 二元组 keyset 分页保证不重不漏。
脱敏：不输出 user_id / context_summary。成交 LEFT JOIN 任务——任务删除后
PaperOrder 仍保留（task_id 无外键），孤儿成交继续可见；决策随任务级联删除。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.decision import AITradingDecision
from app.models.ai_trading.task import AITradingTask
from app.models.paper.order import PaperOrder

_MAX_LIMIT = 200


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _trade_ts(order: PaperOrder) -> datetime | None:
    return order.filled_at or order.created_at


async def list_public_decisions(
    session: AsyncSession,
    since_ts: datetime | None = None,
    since_id: UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """全局分析记录流（升序），跨所有任务所有用户（脱敏）

    传 since_ts+since_id 时仅返回 (created_at, id) 大于游标的记录。
    """
    safe_limit = max(1, min(int(limit), _MAX_LIMIT))
    stmt = (
        select(
            AITradingDecision,
            AITradingTask.name,
            AITradingTask.symbol,
            AITradingTask.symbol_name,
        )
        .join(AITradingTask, AITradingTask.id == AITradingDecision.task_id)
        .order_by(AITradingDecision.created_at.asc(), AITradingDecision.id.asc())
        .limit(safe_limit + 1)
    )
    if since_ts is not None:
        stmt = stmt.where(
            tuple_(AITradingDecision.created_at, AITradingDecision.id)
            > tuple_(_ensure_utc(since_ts), since_id)
        )
    rows = list((await session.execute(stmt)).all())
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]

    items = [
        {
            "id": str(d.id),
            "task_id": str(d.task_id),
            "task_name": task_name,
            "symbol": symbol,
            "symbol_name": symbol_name,
            "timeframe": d.timeframe,
            "trigger_type": d.trigger_type,
            "action": d.action,
            "reason": d.reason,
            "bar_time": d.bar_time,
            "model_output": d.model_output,
            "order_id": str(d.order_id) if d.order_id else None,
            "created_at": _iso(d.created_at),
        }
        for d, task_name, symbol, symbol_name in rows
    ]
    return _cursor_page(items, has_more, "created_at")


async def list_public_trades(
    session: AsyncSession,
    since_ts: datetime | None = None,
    since_id: UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """全局成交流（升序）——按 task_id 直查已成交委托

    与内部 /tasks/{id}/trades（经 decision 关联）不同：强平/会话平仓等
    无决策成交也包含，口径同 /tasks/{id}/trade-marks。附带手续费/保证金字段。
    """
    safe_limit = max(1, min(int(limit), _MAX_LIMIT))
    ts_expr = func.coalesce(PaperOrder.filled_at, PaperOrder.created_at)
    stmt = (
        select(PaperOrder, AITradingTask.name)
        .join(AITradingTask, AITradingTask.id == PaperOrder.task_id, isouter=True)
        .where(
            PaperOrder.task_id.is_not(None),
            PaperOrder.status == "filled",
            PaperOrder.filled_qty > 0,
        )
        .order_by(ts_expr.asc(), PaperOrder.id.asc())
        .limit(safe_limit + 1)
    )
    if since_ts is not None:
        stmt = stmt.where(
            tuple_(ts_expr, PaperOrder.id)
            > tuple_(_ensure_utc(since_ts), since_id)
        )
    rows = list((await session.execute(stmt)).all())
    has_more = len(rows) > safe_limit
    rows = rows[:safe_limit]

    items = []
    for order, task_name in rows:
        ts = _trade_ts(order)
        items.append(
            {
                "id": str(order.id),
                "order_id": str(order.id),
                "task_id": str(order.task_id),
                "task_name": task_name,
                "ts": _iso(ts),
                "symbol": order.symbol,
                "symbol_name": order.symbol_name,
                "direction": order.direction,
                "offset": order.offset,
                "volume": int(order.filled_qty or 0),
                "price": float(order.price),
                "fee": float(order.fee or 0),
                "realized_pnl": float(order.realized_pnl or 0),
                "multiplier": order.multiplier,
                "margin_rate": float(order.margin_rate or 0),
                "frozen_margin": float(order.frozen_margin or 0),
                "status": order.status,
                "filled_at": _iso(order.filled_at),
            }
        )
    return _cursor_page(items, has_more, "ts")


def _cursor_page(
    items: list[dict[str, Any]], has_more: bool, ts_field: str
) -> dict[str, Any]:
    """公共分页壳：回显下一页游标，调用方原样回传即不重不漏"""
    last = items[-1] if items else None
    return {
        "items": items,
        "count": len(items),
        "has_more": has_more,
        "next_since_ts": last[ts_field] if last else None,
        "next_since_id": last["id"] if last else None,
    }
