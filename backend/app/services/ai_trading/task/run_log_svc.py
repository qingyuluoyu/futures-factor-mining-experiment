"""任务运行日志服务 —— 写入（best-effort）与查询

写入绝不抛出：日志失败只打 warning，不能阻塞交易主路径。
detail 按 strategy_type 分层（因子组合快照 / 模型输出 / 账户状态），
是后续因子衰减分析、组合再优化、AI 决策复盘的统一数据链。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading import AITradingRunLog, AITradingTask

logger = logging.getLogger("qihuo.run_log")

_REASON_MAX = 2000


def _trim_reason(reason: Any) -> str:
    s = str(reason or "")
    return s[:_REASON_MAX]


async def write_run_log(
    session: AsyncSession,
    task: AITradingTask,
    *,
    level: str = "info",
    event: str = "signal",
    action: str = "",
    order_id: UUID | None = None,
    reason: str = "",
    detail: dict[str, Any] | None = None,
    bar_time: str | None = None,
) -> None:
    """写一条运行日志（best-effort：失败仅告警，不抛出）

    task 为普通位置参数：engine/loop 既有调用均为 write_run_log(session, task, ...)，
    曾因 keyword-only 化导致参数绑定 TypeError 逃出 best-effort 保护，
    使整次评估失败（run-once 500 / 循环重复下单风险）。
    """
    try:
        row = AITradingRunLog(
            task_id=task.id,
            user_id=task.user_id,
            bar_time=bar_time or getattr(task, "last_bar_time", None),
            timeframe=task.timeframe or "",
            symbol=task.symbol or "",
            strategy_type=task.strategy_type or "",
            side_mode=task.side_mode or "both",
            level=level,
            event=event,
            action=action or "",
            order_id=order_id,
            reason=_trim_reason(reason),
            detail=detail,
        )
        session.add(row)
        await session.flush()
    except Exception:
        logger.warning(
            "运行日志写入失败 task=%s event=%s（不影响交易）",
            getattr(task, "id", "?"), event, exc_info=True,
        )


async def list_run_logs(
    session: AsyncSession,
    *,
    task_id: UUID | None = None,
    user_id: UUID | None = None,
    level: str | None = None,
    event: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """查询运行日志（时间倒序），返回 (rows, total)"""
    stmt = select(AITradingRunLog)
    if task_id is not None:
        stmt = stmt.where(AITradingRunLog.task_id == task_id)
    if user_id is not None:
        stmt = stmt.where(AITradingRunLog.user_id == user_id)
    if level:
        stmt = stmt.where(AITradingRunLog.level == level)
    if event:
        stmt = stmt.where(AITradingRunLog.event == event)
    total = (
        await session.execute(
            select(func.count()).select_from(stmt.subquery())
        )
    ).scalar() or 0
    rows = (
        await session.execute(
            stmt.order_by(desc(AITradingRunLog.created_at)).limit(limit).offset(offset)
        )
    ).scalars().all()
    out = []
    for r in rows:
        out.append(
            {
                "id": str(r.id),
                "task_id": str(r.task_id),
                "user_id": str(r.user_id),
                "bar_time": r.bar_time,
                "timeframe": r.timeframe,
                "symbol": r.symbol,
                "strategy_type": r.strategy_type,
                "side_mode": r.side_mode,
                "level": r.level,
                "event": r.event,
                "action": r.action,
                "order_id": str(r.order_id) if r.order_id else None,
                "reason": r.reason,
                "detail": dict(r.detail) if isinstance(r.detail, dict) else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out, int(total)
