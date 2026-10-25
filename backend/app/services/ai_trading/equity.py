"""权益快照写入与查询"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.decision import AITradingDecision
from app.models.ai_trading.equity import AITradingEquityPoint
from app.models.ai_trading.task import AITradingTask
from app.models.paper.order import PaperOrder
from app.services.paper.account_svc import get_account_summary


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: float | int | str | Decimal) -> Decimal:
    return Decimal(str(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _realized_events_by_task(
    session: AsyncSession,
    user_id: UUID,
    task_ids: list[UUID],
) -> dict[UUID, list[tuple[datetime, Decimal]]]:
    """Return filled close PnL events for new and legacy equity points."""
    if not task_ids:
        return {}
    stmt = (
        select(
            AITradingDecision.task_id,
            PaperOrder.filled_at,
            PaperOrder.updated_at,
            PaperOrder.created_at,
            PaperOrder.realized_pnl,
        )
        .select_from(AITradingDecision)
        .join(PaperOrder, PaperOrder.id == AITradingDecision.order_id)
        .where(
            AITradingDecision.user_id == user_id,
            AITradingDecision.task_id.in_(task_ids),
            AITradingDecision.order_id.is_not(None),
            PaperOrder.offset == "close",
            PaperOrder.status == "filled",
        )
    )
    rows = (await session.execute(stmt)).all()
    events: dict[UUID, list[tuple[datetime, Decimal]]] = {}
    for task_id, filled_at, updated_at, created_at, pnl in rows:
        event_time = filled_at or updated_at or created_at
        if event_time is None:
            continue
        events.setdefault(task_id, []).append((_utc(event_time), _dec(pnl or 0)))
    for task_events in events.values():
        task_events.sort(key=lambda item: item[0])
    return events


async def snapshot_equity(
    session: AsyncSession,
    task: AITradingTask,
    redis_conn: object | None = None,
) -> dict:
    """写入一条权益点并返回 dict

    cash_delta / unrealized_pnl 仅计「本任务品种」浮动盈亏，
    不含手续费，也不把共用账户其他品种盈亏串进来。
    """
    from app.services.ai_trading.task.position_pnl import (
        get_task_position_pnl,
        source_for_task,
    )

    mode = str(getattr(task, "trading_mode", None) or "live")
    summary = await get_account_summary(session, task.user_id, mode)
    account_equity = _dec(summary.get("total_equity") or 0)
    pnl = await get_task_position_pnl(
        session,
        task.user_id,
        task.symbol,
        redis_conn,
        source_for_task(task),
        task_id=task.id,
    )
    task_float = _dec(pnl.get("unrealized_pnl") or 0)
    events = await _realized_events_by_task(session, task.user_id, [task.id])
    realized = sum((value for _, value in events.get(task.id, [])), Decimal("0"))
    total_pnl = realized + task_float
    # 曲线保存任务总收益；equity 字段仍记账户权益便于排查
    point = AITradingEquityPoint(
        task_id=task.id,
        user_id=task.user_id,
        ts=_now_utc(),
        equity=account_equity,
        cash_delta=total_pnl,
        realized_pnl=realized,
        unrealized_pnl=task_float,
    )
    session.add(point)
    await session.flush()
    return {
        "task_id": str(task.id),
        "ts": point.ts.isoformat().replace("+00:00", "Z"),
        "equity": float(account_equity),
        "cash_delta": float(total_pnl),
        "realized_pnl": float(realized),
        "unrealized_pnl": float(task_float),
    }


async def list_equity_series(
    session: AsyncSession,
    user_id: UUID,
    task_ids: list[UUID],
    limit_per_task: int,
) -> dict[str, list[dict]]:
    """按任务聚合权益点（每个任务取最近 N 点，时间升序）"""
    series: dict[str, list[dict]] = {}
    safe_limit = max(10, min(limit_per_task, 2000))
    events_by_task = await _realized_events_by_task(session, user_id, task_ids)
    for tid in task_ids:
        stmt = (
            select(AITradingEquityPoint)
            .where(
                AITradingEquityPoint.user_id == user_id,
                AITradingEquityPoint.task_id == tid,
            )
            .order_by(AITradingEquityPoint.ts.desc())
            .limit(safe_limit)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        task_events = events_by_task.get(tid, [])
        event_index = 0
        realized = Decimal("0")
        points: list[dict] = []
        for row in rows:
            point_time = _utc(row.ts)
            while (
                event_index < len(task_events)
                and task_events[event_index][0] <= point_time
            ):
                realized += task_events[event_index][1]
                event_index += 1
            point_unrealized = _dec(row.unrealized_pnl or 0)
            point_total = realized + point_unrealized
            points.append(
                {
                    "task_id": str(row.task_id),
                    "ts": row.ts.isoformat().replace("+00:00", "Z") if row.ts else "",
                    "equity": float(row.equity),
                    "cash_delta": float(point_total),
                    "realized_pnl": float(realized),
                    "unrealized_pnl": float(point_unrealized),
                }
            )
        series[str(tid)] = points
    return series


def cash_delta_pct(task: AITradingTask, equity: float) -> float | None:
    """相对 baseline 的权益变化百分比"""
    baseline = float(task.equity_baseline or 0)
    if baseline <= 0:
        return None
    return (equity - baseline) / baseline * 100.0
