"""AI 交易任务启停状态机"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.task import AITradingTask
from app.models.user import User
from app.services.ai_trading.core.bar_align import next_bar_boundary
from app.services.ai_trading.equity import snapshot_equity
from app.services.ai_trading.task.task_svc import get_task
from app.services.paper.account_svc import get_account_summary
from app.services.quota.quota_svc import get_effective_limits

logger = logging.getLogger("qihuo.ai_trading")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _accumulate_runtime(task: AITradingTask, now: datetime) -> None:
    """离开 running 时累加本次运行段到 runtime_seconds，并清空段起点。"""
    start = task.run_started_at
    if start is not None:
        delta = int((now - start).total_seconds())
        if delta > 0:
            task.runtime_seconds = int(task.runtime_seconds or 0) + delta
        task.run_started_at = None


def _start_run_segment(task: AITradingTask, now: datetime) -> None:
    """进入 running 时记录本次运行段起点。"""
    task.run_started_at = now


async def _count_running(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> int:
    from app.core.trading_mode import normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    stmt = select(func.count()).select_from(AITradingTask).where(
        AITradingTask.user_id == user_id,
        AITradingTask.trading_mode == mode,
        AITradingTask.status == "running",
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def start_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """开始/恢复任务

    若当前非交易时段，标记为休市暂停（pause_reason=market_closed），
    开盘后由 session_auto 自动恢复。
    """
    from app.services.trading_hours import get_session_status

    task, data = await get_task(session, user_id, task_id, trading_mode=trading_mode)
    mode = str(getattr(task, "trading_mode", None) or trading_mode or "live")
    # stopped 仅在可编辑条件下允许重新启动（修改后会变 paused；
    # 若未改直接点开始：要求无持仓）
    if task.status == "stopped":
        if data.get("has_open_position"):
            raise HTTPException(
                status_code=400,
                detail="已结束任务且该品种仍有持仓，请平仓或修改配置后再启动",
            )
        # 允许重新启动：清掉 stopped 标记
        task.status = "paused"
        task.pause_reason = "user"
        task.stopped_at = None
    if task.status == "running":
        _, data = await get_task(
            session, user_id, task_id, trading_mode=mode
        )
        return data
    # 运行任务数配额：admin 不限；普通用户用生效配额（max_tasks_override 或全局默认）
    user = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is not None:
        limits = await get_effective_limits(session, user)
        max_running = limits.get("max_tasks")
        if max_running is not None:
            running = await _count_running(session, user_id, mode)
            if running >= int(max_running):
                raise HTTPException(
                    status_code=400,
                    detail=f"同时运行任务不能超过 {max_running} 个",
                )
    summary = await get_account_summary(session, user_id, mode)
    now = _now_utc()
    if task.started_at is None:
        task.equity_baseline = Decimal(str(summary.get("total_equity") or 0))
        task.started_at = now

    symbol_open = bool(get_session_status(task.symbol).get("is_open"))
    if symbol_open:
        task.status = "running"
        task.pause_reason = None
        task.paused_at = None
        task.note = None
        task.next_bar_ts = next_bar_boundary(now, task.timeframe)
        _start_run_segment(task, now)
    else:
        # 用户意图是「要跑」：挂到休市暂停，开盘自动启动
        task.status = "paused"
        task.pause_reason = "market_closed"
        task.paused_at = now
        task.note = "非交易时段已挂起，开盘后自动开始"
        task.next_bar_ts = None
    task.updated_at = now
    await session.flush()
    await snapshot_equity(session, task, None)
    _, data = await get_task(
        session, user_id, task_id, None, mode
    )
    return data


async def pause_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """用户手动暂停任务（开盘后不会自动恢复）"""
    task, _ = await get_task(
        session, user_id, task_id, trading_mode=trading_mode
    )
    mode = str(getattr(task, "trading_mode", None) or trading_mode or "live")
    if task.status != "running":
        raise HTTPException(status_code=400, detail="仅运行中任务可暂停")
    now = _now_utc()
    _accumulate_runtime(task, now)
    task.status = "paused"
    task.pause_reason = "user"
    task.paused_at = now
    task.updated_at = now
    task.note = "用户手动暂停"
    await session.flush()
    _, data = await get_task(
        session, user_id, task_id, trading_mode=mode
    )
    return data


async def stop_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    force_close: bool,
    redis_conn: object | None,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """结束任务；可选强制平该品种仓"""
    task, _ = await get_task(
        session, user_id, task_id, trading_mode=trading_mode
    )
    mode = str(getattr(task, "trading_mode", None) or trading_mode or "live")
    if task.status == "stopped":
        _, data = await get_task(
            session, user_id, task_id, trading_mode=mode
        )
        return data

    do_close = force_close or bool(task.close_on_stop)
    close_failed = False
    if do_close:
        from app.services.ai_trading.engine import force_close_symbol

        try:
            await force_close_symbol(
                session, task, redis_conn, trigger_type="manual"
            )
        except Exception as exc:
            # 平仓失败（常见于停盘期间 paper 层拒绝下单）不阻塞任务结束。
            # 任务仍转为 stopped，持仓保留，note 记录原因；
            # 用户可待开盘手动平仓，或在无持仓后删除任务。
            # 这避免了"停盘结束任务 → 平仓失败 → 任务卡住删不掉"的死锁。
            close_failed = True
            task.note = f"结束任务时平仓失败（{str(exc)[:80]}），持仓保留待开盘处理"
            logger.warning("stop_task 平仓失败 task=%s: %s", task_id, exc)

    # 释放 AI 资金仓预留
    # 资金仓在 stop 时不退回（暂留），仅 delete 时 release_task_capital 退回主账户
    now = _now_utc()
    _accumulate_runtime(task, now)
    task.position_opened_at = None
    task.status = "stopped"
    task.stopped_at = now
    task.updated_at = now
    await session.flush()
    await snapshot_equity(session, task, redis_conn)
    _, data = await get_task(
        session, user_id, task_id, redis_conn, mode
    )
    return data
