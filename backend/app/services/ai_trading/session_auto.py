"""AI 交易交易时段自动启停

- 非交易时段：running → paused(pause_reason=market_closed)
- 开盘后：仅恢复 pause_reason=market_closed 的任务，不碰用户手动暂停
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.task import AITradingTask
from app.services.ai_trading.core.bar_align import next_bar_boundary
from app.services.ai_trading.task.task_lifecycle import (
    _accumulate_runtime,
    _start_run_segment,
)
from app.services.trading_hours import get_session_status, is_trading_hours

logger = logging.getLogger("qihuo.ai_trading")

PAUSE_MARKET = "market_closed"
PAUSE_USER = "user"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def sync_tasks_with_session(
    session: AsyncSession,
    redis_conn: object | None,
) -> dict[str, int]:
    """按交易时段同步任务状态，返回 {paused, resumed} 计数"""
    paused_n = 0
    resumed_n = 0
    now = _now_utc()
    market_open_coarse = is_trading_hours()

    # 1) 休市：关闭仍在 running 的任务（按合约精确时段）
    stmt_run = select(AITradingTask).where(AITradingTask.status == "running")
    running = list((await session.execute(stmt_run)).scalars().all())
    for task in running:
        timetable_closed = not bool(get_session_status(task.symbol).get("is_open"))
        open_for_symbol = not timetable_closed
        if open_for_symbol:
            # 节假日/异常闭市兜底：时段表开盘但盘口长时间无变化（价/量/
            # 买卖档冻结）→ 视为休市。法定节假日在日历上是工作日，时段表
            # 判不出；数据源休市期间仍可能推冻结快照，故按值变化判定
            from app.services.market_motion import is_symbol_motion_stale

            if await is_symbol_motion_stale(
                redis_conn, task.symbol, trading_mode=task.trading_mode
            ):
                open_for_symbol = False
        if open_for_symbol:
            continue
        # 收盘平仓已由 evaluate_task 在日盘 14:55（市场仍开时）执行；
        # 此处市场已关、下单必被交易时段校验拒绝，故不再 force_close，仅暂停。
        # 若仍带仓且配置了 session_close，标注待下个交易日 14:55 补平。
        has_session_close = bool((task.close_rules or {}).get("session_close"))
        _accumulate_runtime(task, now)
        task.status = "paused"
        task.pause_reason = PAUSE_MARKET
        task.paused_at = now
        task.updated_at = now
        if timetable_closed:
            task.note = (
                "收盘前未平（当时可能未 running），待下个交易日 14:55 补平"
                if has_session_close
                else "非交易时段自动暂停，开盘后将自动恢复"
            )
        else:
            # 时段表开盘但盘口冻结：节假日/异常闭市
            task.note = "盘口长时间无变化（节假日/休市），自动暂停；开盘后自动恢复"
        paused_n += 1

    # 2) 开盘：仅恢复「因休市自动暂停」的任务（盘口需已恢复变化）
    if market_open_coarse or running:
        stmt_paused = select(AITradingTask).where(
            AITradingTask.status == "paused",
            AITradingTask.pause_reason == PAUSE_MARKET,
        )
        auto_paused = list((await session.execute(stmt_paused)).scalars().all())
        for task in auto_paused:
            open_for_symbol = bool(get_session_status(task.symbol).get("is_open"))
            if open_for_symbol:
                from app.services.market_motion import is_symbol_motion_stale

                if await is_symbol_motion_stale(
                    redis_conn, task.symbol, trading_mode=task.trading_mode
                ):
                    # 节假日盘中：时段表开盘但盘口仍冻结，继续暂停
                    open_for_symbol = False
            if not open_for_symbol:
                continue
            task.status = "running"
            task.pause_reason = None
            task.paused_at = None
            _start_run_segment(task, now)
            task.updated_at = now
            task.next_bar_ts = next_bar_boundary(now, task.timeframe)
            task.note = "开盘自动恢复运行"
            resumed_n += 1

    if paused_n or resumed_n:
        await session.flush()
        logger.info(
            "交易时段同步：休市暂停 %d，开盘恢复 %d",
            paused_n,
            resumed_n,
        )
    return {"paused": paused_n, "resumed": resumed_n}
