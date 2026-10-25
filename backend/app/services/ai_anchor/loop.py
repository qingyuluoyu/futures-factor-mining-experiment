"""AI 看盘主播后台循环 —— 由 scheduler 选主 worker 启动

每 15s 扫描一次到期（next_run_at <= now）的 running 任务并评估；
闭盘由引擎内部顺延跳过（不播报），开盘后自动恢复。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.models.ai_anchor import AIAnchorTask
from app.services.ai_anchor.engine import evaluate_anchor
from app.services.ai_anchor.svc import utc_now

logger = logging.getLogger("qihuo.ai_anchor")

# 扫描间隔（秒）：启动/恢复后最多延迟一个扫描周期出首轮播报
_SCAN_INTERVAL = 15.0
# 异常兜底顺延下限（分钟）：防止评估抛错后立即到期死循环
_FAIL_RETRY_MIN = 5


async def _process_due_tasks(session_factory: Any, redis_conn: Any) -> int:
    """评估所有到期的 running 主播任务（单批内串行，避免并发打模型）。"""
    if session_factory is None:
        return 0
    processed = 0
    async with session_factory() as session:
        try:
            now = utc_now()
            stmt = select(AIAnchorTask).where(
                AIAnchorTask.status == "running",
                AIAnchorTask.next_run_at <= now,
            )
            tasks = list((await session.execute(stmt)).scalars().all())
            for task in tasks:
                try:
                    result = await evaluate_anchor(session, task, redis_conn)
                    processed += 1
                    if result.get("skipped") == "market_closed":
                        logger.debug("主播闭盘顺延 task=%s", task.id)
                except Exception as exc:
                    logger.exception("主播评估失败 task=%s", task.id)
                    # 引擎内已捕获模型/数据异常；这里兜底评估级异常，
                    # 顺延避免同一任务在扫描间隔内反复失败
                    task.next_run_at = utc_now() + timedelta(
                        minutes=max(_FAIL_RETRY_MIN, int(task.interval_minutes))
                    )
                    task.note = f"最近一次评估异常: {type(exc).__name__}"
                    await session.flush()
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("主播批处理失败")
    return processed


async def ai_anchor_loop(session_factory: Any, redis_conn: Any) -> None:
    """后台循环主体（与 ai_trading 循环同生命周期约定）。"""
    logger.info("AI 看盘主播循环已启动（扫描间隔 %.0fs）", _SCAN_INTERVAL)
    while True:
        try:
            await _process_due_tasks(session_factory, redis_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("AI 看盘主播循环异常")
        await asyncio.sleep(_SCAN_INTERVAL)


_loop_task: asyncio.Task | None = None


def start_ai_anchor_loop(session_factory: Any, redis_conn: Any) -> asyncio.Task:
    """启动后台任务（幂等）。"""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        return _loop_task
    _loop_task = asyncio.create_task(
        ai_anchor_loop(session_factory, redis_conn),
        name="ai-anchor-loop",
    )
    return _loop_task
