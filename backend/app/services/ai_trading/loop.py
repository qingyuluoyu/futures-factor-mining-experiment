"""AI 交易后台循环 —— 由 scheduler 选主 worker 启动"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from app.models.ai_trading.task import AITradingTask
from app.services.ai_trading.engine import evaluate_task
from app.services.ai_trading.session_auto import sync_tasks_with_session
from app.services.trading_hours import is_trading_hours

logger = logging.getLogger("qihuo.ai_trading")

# 循环间隔
_INTERVAL_TRADING = 2.0
# 非交易时段不拉取数据，仅低频检查是否开盘（避免周末高频请求触发数据源封禁）
_INTERVAL_IDLE = 60.0

_loop_task: asyncio.Task | None = None


async def _process_all_running(
    session_factory: Any,
    redis_conn: Any,
) -> int:
    """先做时段同步，再评估 running 任务"""
    if session_factory is None:
        return 0
    processed = 0
    async with session_factory() as session:
        try:
            # 休市自动暂停 / 开盘自动恢复
            await sync_tasks_with_session(session, redis_conn)

            stmt = select(AITradingTask).where(AITradingTask.status == "running")
            result = await session.execute(stmt)
            tasks = list(result.scalars().all())
            for task in tasks:
                try:
                    await evaluate_task(session, task, redis_conn, force=False)
                    processed += 1
                except Exception as _exc:
                    logger.exception("评估任务失败 task=%s", task.id)
                    try:
                        from app.services.ai_trading.task.run_log_svc import (
                            write_run_log,
                        )

                        await write_run_log(
                            session, task,
                            level="error",
                            event="error",
                            reason=f"评估异常: {type(_exc).__name__}: {_exc}",
                            detail={"error_type": type(_exc).__name__},
                        )
                        task.note = "最近一次评估异常，将自动重试"
                        await session.flush()
                    except Exception:
                        pass
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("AI 交易批处理失败")
    return processed


async def ai_trading_loop(
    session_factory: Any,
    redis_conn: Any,
) -> None:
    """后台循环主体

    非交易时段（周末/节假日/休市）：只同步任务状态（休市自动暂停），
    不评估任务、不拉取 K 线 —— 避免周末高频请求触发新浪/天勤等数据源 IP 封禁。
    """
    logger.info("AI 交易循环已启动（非交易时段不拉取，开盘自动恢复）")
    while True:
        if not is_trading_hours():
            try:
                # 仅状态同步（不涉及行情拉取），确保休市自动暂停、开盘自动恢复
                if session_factory is not None:
                    async with session_factory() as session:
                        await sync_tasks_with_session(session, redis_conn)
                        await session.commit()
            except Exception:
                logger.exception("AI 交易时段同步失败")
            await asyncio.sleep(_INTERVAL_IDLE)
            continue
        try:
            n = await _process_all_running(session_factory, redis_conn)
            if n:
                logger.debug("AI 交易本轮扫描 %d 个 running 任务", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI 交易循环异常")
        await asyncio.sleep(_INTERVAL_TRADING)


def start_ai_trading_loop(
    session_factory: Any,
    redis_conn: Any,
) -> asyncio.Task:
    """启动后台任务（幂等）"""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        return _loop_task
    _loop_task = asyncio.create_task(
        ai_trading_loop(session_factory, redis_conn),
        name="ai-trading-loop",
    )
    return _loop_task


async def stop_ai_trading_loop() -> None:
    """停止后台任务"""
    global _loop_task
    if _loop_task is None:
        return
    _loop_task.cancel()
    try:
        await _loop_task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("停止 AI 交易循环异常")
    _loop_task = None
    logger.info("AI 交易循环已停止")
