"""管理员查看用户 AI 任务 / 决策 / 盈亏（按 trading_mode）"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trading_mode import normalize_trading_mode
from app.models.ai_trading.decision import AITradingDecision
from app.models.ai_trading.run_log import AITradingRunLog
from app.models.ai_trading.equity import AITradingEquityPoint
from app.models.ai_trading.task import AITradingTask
from app.models.paper.account import PaperAccount
from app.models.paper.position import PaperPosition
from app.services.admin.users_svc import get_user_or_404
from app.services.paper.account_svc import get_account_summary


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _mode(trading_mode: str | None) -> str:
    return normalize_trading_mode(trading_mode)


async def list_user_ai_tasks(
    session: AsyncSession,
    user_id: UUID,
    limit: int,
    offset: int,
    trading_mode: str = "live",
) -> dict:
    """用户 AI 交易任务列表 + 简单盈亏（分盘）"""
    mode = _mode(trading_mode)
    await get_user_or_404(session, user_id)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    mode_filter = (
        AITradingTask.user_id == user_id,
        AITradingTask.trading_mode == mode,
    )
    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(AITradingTask)
                .where(*mode_filter)
            )
        ).scalar_one()
    )
    tasks = list(
        (
            await session.execute(
                select(AITradingTask)
                .where(*mode_filter)
                .order_by(AITradingTask.updated_at.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
        )
        .scalars()
        .all()
    )

    items = []
    for t in tasks:
        items.append(await _task_item(session, user_id, t, mode))
    return {"total": total, "trading_mode": mode, "items": items}


async def _task_item(
    session: AsyncSession,
    user_id: UUID,
    t: AITradingTask,
    mode: str,
) -> dict:
    """单任务摘要（权益 + 同盘同合约持仓）"""
    eq_stmt = (
        select(AITradingEquityPoint)
        .where(AITradingEquityPoint.task_id == t.id)
        .order_by(AITradingEquityPoint.ts.desc())
        .limit(1)
    )
    last_eq = (await session.execute(eq_stmt)).scalar_one_or_none()
    last_equity = float(last_eq.equity) if last_eq is not None else None
    baseline = float(t.equity_baseline or 0)
    cash_delta = None
    if last_eq is not None:
        cash_delta = float(last_eq.cash_delta)
    elif last_equity is not None:
        cash_delta = round(last_equity - baseline, 2)

    pos_stmt = (
        select(PaperPosition)
        .join(PaperAccount, PaperAccount.id == PaperPosition.account_id)
        .where(
            PaperPosition.user_id == user_id,
            PaperPosition.symbol == t.symbol,
            PaperPosition.quantity > 0,
            PaperPosition.task_id == t.id,
            PaperAccount.trading_mode == mode,
        )
    )
    positions = list((await session.execute(pos_stmt)).scalars().all())
    pos_summary = [
        {
            "direction": p.direction,
            "quantity": int(p.quantity),
            "avg_price": float(p.avg_price),
            "margin": float(p.margin),
        }
        for p in positions
    ]
    return {
        "id": str(t.id),
        "name": t.name,
        "symbol": t.symbol,
        "symbol_name": t.symbol_name,
        "timeframe": t.timeframe,
        "strategy_type": getattr(t, "strategy_type", None) or "ai",
        "trading_mode": mode,
        "status": t.status,
        "pause_reason": t.pause_reason,
        "equity_baseline": baseline,
        "last_equity": last_equity,
        "cash_delta": cash_delta,
        "last_run_at": _iso(t.last_run_at),
        "started_at": _iso(t.started_at),
        "stopped_at": _iso(t.stopped_at),
        "note": t.note,
        "created_at": _iso(t.created_at) or "",
        "positions": pos_summary,
    }


async def list_user_ai_decisions(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID | None,
    limit: int,
    offset: int,
    trading_mode: str = "live",
) -> dict:
    """用户 AI 决策日志（仅该盘任务）"""
    mode = _mode(trading_mode)
    await get_user_or_404(session, user_id)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    filters = [
        AITradingDecision.user_id == user_id,
        AITradingTask.trading_mode == mode,
    ]
    if task_id is not None:
        filters.append(AITradingDecision.task_id == task_id)

    count_stmt = (
        select(func.count())
        .select_from(AITradingDecision)
        .join(AITradingTask, AITradingTask.id == AITradingDecision.task_id)
        .where(*filters)
    )
    list_stmt = (
        select(AITradingDecision)
        .join(AITradingTask, AITradingTask.id == AITradingDecision.task_id)
        .where(*filters)
        .order_by(AITradingDecision.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = list((await session.execute(list_stmt)).scalars().all())
    items = [
        {
            "id": str(d.id),
            "task_id": str(d.task_id),
            "bar_time": d.bar_time,
            "timeframe": d.timeframe,
            "trigger_type": d.trigger_type,
            "action": d.action,
            "reason": d.reason,
            "order_id": str(d.order_id) if d.order_id else None,
            "model_output": d.model_output,
            "context_summary": d.context_summary,
            "created_at": _iso(d.created_at) or "",
        }
        for d in rows
    ]
    return {"total": total, "trading_mode": mode, "items": items}


async def get_user_pnl_summary(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """用户盈亏汇总（指定盘）"""
    mode = _mode(trading_mode)
    await get_user_or_404(session, user_id)
    account = await get_account_summary(session, user_id, mode)

    tasks = list(
        (
            await session.execute(
                select(AITradingTask).where(
                    AITradingTask.user_id == user_id,
                    AITradingTask.trading_mode == mode,
                )
            )
        )
        .scalars()
        .all()
    )
    task_pnls = []
    total_delta = Decimal("0")
    for t in tasks:
        eq_stmt = (
            select(AITradingEquityPoint)
            .where(AITradingEquityPoint.task_id == t.id)
            .order_by(AITradingEquityPoint.ts.desc())
            .limit(1)
        )
        last_eq = (await session.execute(eq_stmt)).scalar_one_or_none()
        delta = (
            Decimal(str(last_eq.cash_delta))
            if last_eq is not None
            else Decimal("0")
        )
        total_delta += delta
        task_pnls.append(
            {
                "task_id": str(t.id),
                "name": t.name,
                "symbol": t.symbol,
                "status": t.status,
                "cash_delta": float(delta),
            }
        )

    return {
        "trading_mode": mode,
        "paper_realized_pnl": float(account.get("realized_pnl") or 0),
        "paper_total_equity": float(account.get("total_equity") or 0),
        "paper_available": float(account.get("available_margin") or 0),
        "ai_tasks_cash_delta_sum": float(total_delta),
        "tasks": task_pnls,
    }


async def list_user_run_logs(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID | None,
    level: str | None,
    event: str | None,
    limit: int,
    offset: int,
    trading_mode: str = "live",
) -> dict:
    """用户任务运行日志（run_logs，信号/下单/异常事件流）"""
    mode = _mode(trading_mode)
    await get_user_or_404(session, user_id)
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    filters = [
        AITradingRunLog.user_id == user_id,
        AITradingTask.trading_mode == mode,
    ]
    if task_id is not None:
        filters.append(AITradingRunLog.task_id == task_id)
    if level:
        filters.append(AITradingRunLog.level == level)
    if event:
        filters.append(AITradingRunLog.event == event)

    count_stmt = (
        select(func.count())
        .select_from(AITradingRunLog)
        .join(AITradingTask, AITradingTask.id == AITradingRunLog.task_id)
        .where(*filters)
    )
    list_stmt = (
        select(AITradingRunLog)
        .join(AITradingTask, AITradingTask.id == AITradingRunLog.task_id)
        .where(*filters)
        .order_by(AITradingRunLog.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = list((await session.execute(list_stmt)).scalars().all())
    items = [
        {
            "id": str(r.id),
            "task_id": str(r.task_id),
            "task_name": "",  # 前端用任务下拉补名
            "bar_time": r.bar_time,
            "timeframe": r.timeframe,
            "symbol": r.symbol,
            "strategy_type": r.strategy_type,
            "level": r.level,
            "event": r.event,
            "action": r.action,
            "order_id": str(r.order_id) if r.order_id else None,
            "reason": r.reason,
            "detail": r.detail,
            "created_at": _iso(r.created_at) or "",
        }
        for r in rows
    ]
    return {"total": total, "trading_mode": mode, "items": items}
