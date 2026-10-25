"""首页公开展示 —— 运行中 AI 任务收益（脱敏）"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.decision import AITradingDecision
from app.models.ai_trading.equity import AITradingEquityPoint
from app.models.ai_trading.task import AITradingTask
from app.models.paper.order import PaperOrder
from app.services.ai_trading.task.position_pnl import (
    get_task_position_pnl,
    source_for_task,
)
from app.services.ai_trading.task.task_svc import _load_model_meta, task_to_dict


async def list_showcase(
    session: AsyncSession,
    redis_conn: object | None,
    limit: int,
    trading_mode: str | None,
    include_equity: bool = True,
    equity_from: datetime | None = None,
    equity_to: datetime | None = None,
) -> dict[str, Any]:
    """聚合首页展示数据：任务卡片 + 权益曲线 + 任务总收益柱

    include_equity=False 时跳过权益曲线查询，equity_series 返回空 dict
    （用于对外 WS 轻量摘要推送；收益曲线由客户端走 HTTP 拉）。
    equity_from/equity_to（可选，UTC，左闭右开）指定时权益曲线按时间区间
    返回（升序，每任务上限 2000 点），供展示站历史回填分页拉取；
    不指定时保持"最近 N 点"行为不变。
    """
    safe_limit = max(1, min(int(limit), 24))
    tasks = await _pick_showcase_tasks(session, safe_limit, trading_mode)
    if not tasks:
        return {
            "updated_at": _now_iso(),
            "trading_mode": trading_mode or "all",
            "tasks": [],
            "equity_series": {},
            "profit_items": [],
            "total_realized": 0.0,
            "total_unrealized": 0.0,
            "total_pnl": 0.0,
            "total_today_realized": 0.0,
            "running_count": 0,
            "open_position_count": 0,
        }

    out_tasks: list[dict[str, Any]] = []
    profit_items: list[dict[str, Any]] = []
    sum_realized = 0.0
    sum_unrealized = 0.0
    sum_today_realized = 0.0
    open_count = 0
    running_count = 0
    task_ids: list[UUID] = []

    realized_map = await _realized_by_tasks(session, [t.id for t in tasks])
    today_map = await _today_realized_by_tasks(session, [t.id for t in tasks])

    for task in tasks:
        task_ids.append(task.id)
        try:
            meta = await _load_model_meta(session, task.model_row_id, task.user_id)
        except Exception:
            meta = {
                "model_id": None,
                "display_name": None,
                "provider_name": None,
            }
        pnl = await get_task_position_pnl(
            session, task.user_id, task.symbol, redis_conn,
            source_for_task(task), task_id=task.id,
        )
        qty = int(pnl.get("quantity") or 0)
        unrealized = float(pnl.get("unrealized_pnl") or 0) if qty > 0 else 0.0
        realized = float(realized_map.get(task.id, 0.0))
        today_realized = round(float(today_map.get(task.id, 0.0)), 2)
        total_pnl = round(realized + unrealized, 2)
        if qty > 0:
            open_count += 1
        if task.status == "running":
            running_count += 1
        sum_realized = round(sum_realized + realized, 2)
        sum_unrealized = round(sum_unrealized + unrealized, 2)
        sum_today_realized = round(sum_today_realized + today_realized, 2)

        meta = {
            **meta,
            "has_orders": True,
            "has_open_position": qty > 0,
            "position_direction": pnl.get("direction"),
            "position_qty": qty,
            "position_avg_price": pnl.get("avg_price"),
            "position_last_price": pnl.get("last_price"),
            "position_unrealized": unrealized,
        }
        item = task_to_dict(task, meta, cash_delta=unrealized)
        # 脱敏：不暴露 user_id / 自定义提示词
        item["user_id"] = ""
        item["custom_prompt"] = None
        item["note"] = None
        # 当日已实现（Asia/Shanghai 自然日），消除"当前收入"口径歧义
        item["today_realized"] = today_realized
        out_tasks.append(item)

        profit_items.append(
            {
                "id": str(task.id),
                "task_id": str(task.id),
                "task_name": task.name or task.symbol,
                "symbol": task.symbol,
                "symbol_name": task.symbol_name or task.symbol,
                "direction": str(pnl.get("direction") or ""),
                "offset": "task",
                "price": float(pnl.get("last_price") or 0),
                "filled_qty": qty,
                "fee": 0.0,
                "pnl": unrealized,
                "realized": round(realized, 2),
                "unrealized": round(unrealized, 2),
                "today_realized": today_realized,
                "cumulative": total_pnl,
                "total_pnl": total_pnl,
                "filled_at": "",
                "decision_action": "task_total",
                "kind": "task",
                "status": task.status,
                "has_open_position": qty > 0,
                "strategy_type": task.strategy_type or "ai",
            }
        )

    profit_items.sort(key=lambda x: float(x.get("total_pnl") or 0), reverse=True)
    equity_series = (
        await _list_equity_public(
            session, task_ids, 400, ts_from=equity_from, ts_to=equity_to
        )
        if include_equity
        else {}
    )

    return {
        "updated_at": _now_iso(),
        "trading_mode": trading_mode or "all",
        "tasks": out_tasks,
        "equity_series": equity_series,
        "profit_items": profit_items,
        "total_realized": sum_realized,
        "total_unrealized": sum_unrealized,
        "total_pnl": round(sum_realized + sum_unrealized, 2),
        "total_today_realized": sum_today_realized,
        "running_count": running_count,
        "open_position_count": open_count,
    }


async def _pick_showcase_tasks(
    session: AsyncSession,
    limit: int,
    trading_mode: str | None,
) -> list[AITradingTask]:
    """优先 running，其次有仓/paused，最多 limit 条"""
    mode = (trading_mode or "").strip().lower()
    base = [AITradingTask.status.in_(("running", "paused"))]
    if mode in ("live", "virtual"):
        base.append(AITradingTask.trading_mode == mode)

    run_stmt = (
        select(AITradingTask)
        .where(*base, AITradingTask.status == "running")
        .order_by(AITradingTask.updated_at.desc())
        .limit(limit)
    )
    running = list((await session.execute(run_stmt)).scalars().all())
    if len(running) >= limit:
        return running[:limit]

    remain = limit - len(running)
    run_ids = [t.id for t in running]
    pause_filters = list(base) + [AITradingTask.status == "paused"]
    if run_ids:
        pause_filters.append(AITradingTask.id.not_in(run_ids))
    pause_stmt = (
        select(AITradingTask)
        .where(*pause_filters)
        .order_by(AITradingTask.updated_at.desc())
        .limit(remain)
    )
    paused = list((await session.execute(pause_stmt)).scalars().all())
    return running + paused


async def _realized_by_tasks(
    session: AsyncSession,
    task_ids: list[UUID],
) -> dict[UUID, float]:
    if not task_ids:
        return {}
    stmt = (
        select(
            AITradingDecision.task_id,
            func.coalesce(func.sum(PaperOrder.realized_pnl), 0),
        )
        .select_from(AITradingDecision)
        .join(PaperOrder, PaperOrder.id == AITradingDecision.order_id)
        .where(
            AITradingDecision.task_id.in_(task_ids),
            AITradingDecision.order_id.is_not(None),
            PaperOrder.offset == "close",
            PaperOrder.status == "filled",
        )
        .group_by(AITradingDecision.task_id)
    )
    rows = (await session.execute(stmt)).all()
    return {tid: float(total or 0) for tid, total in rows}


def _cst_day_start_utc() -> datetime:
    """Asia/Shanghai 当日 00:00 对应的 UTC 时刻（当日已实现的日界）"""
    now_cst = datetime.now(ZoneInfo("Asia/Shanghai"))
    day_start_cst = datetime.combine(now_cst.date(), time.min, tzinfo=now_cst.tzinfo)
    return day_start_cst.astimezone(timezone.utc)


async def _today_realized_by_tasks(
    session: AsyncSession,
    task_ids: list[UUID],
) -> dict[UUID, float]:
    """任务当日已实现盈亏（Asia/Shanghai 自然日）

    与 _realized_by_tasks 同口径（decision.order_id 关联的 filled close 单），
    保证 today_realized 与 total_realized 可互相勾稽。
    """
    if not task_ids:
        return {}
    stmt = (
        select(
            AITradingDecision.task_id,
            func.coalesce(func.sum(PaperOrder.realized_pnl), 0),
        )
        .select_from(AITradingDecision)
        .join(PaperOrder, PaperOrder.id == AITradingDecision.order_id)
        .where(
            AITradingDecision.task_id.in_(task_ids),
            AITradingDecision.order_id.is_not(None),
            PaperOrder.offset == "close",
            PaperOrder.status == "filled",
            func.coalesce(PaperOrder.filled_at, PaperOrder.created_at)
            >= _cst_day_start_utc(),
        )
        .group_by(AITradingDecision.task_id)
    )
    rows = (await session.execute(stmt)).all()
    return {tid: float(total or 0) for tid, total in rows}


async def _list_equity_public(
    session: AsyncSession,
    task_ids: list[UUID],
    limit_per_task: int,
    ts_from: datetime | None = None,
    ts_to: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """权益曲线。默认每任务取最近 N 点（时间升序）；指定 ts_from/ts_to
    （左闭右开）时按区间取，每任务上限提升到 2000 点，供历史回填分页。"""
    series: dict[str, list[dict[str, Any]]] = {}
    use_range = ts_from is not None or ts_to is not None
    if use_range:
        safe_limit = 2000
    else:
        safe_limit = max(10, min(limit_per_task, 2000))
    for tid in task_ids:
        stmt = select(AITradingEquityPoint).where(AITradingEquityPoint.task_id == tid)
        if use_range:
            if ts_from is not None:
                stmt = stmt.where(AITradingEquityPoint.ts >= ts_from)
            if ts_to is not None:
                stmt = stmt.where(AITradingEquityPoint.ts < ts_to)
            stmt = stmt.order_by(AITradingEquityPoint.ts.asc()).limit(safe_limit)
            rows = list((await session.execute(stmt)).scalars().all())
        else:
            stmt = (
                stmt.order_by(AITradingEquityPoint.ts.desc())
                .limit(safe_limit)
            )
            rows = list((await session.execute(stmt)).scalars().all())
            rows.reverse()
        series[str(tid)] = [
            {
                "task_id": str(r.task_id),
                "ts": r.ts.isoformat().replace("+00:00", "Z") if r.ts else "",
                "equity": float(r.equity),
                "cash_delta": float(r.cash_delta),
                "realized_pnl": float(r.realized_pnl),
                "unrealized_pnl": float(r.unrealized_pnl),
            }
            for r in rows
        ]
    return series


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
