"""管理员查看用户交易摘要（按 trading_mode 分盘）

AI 相关见 user_inspect_ai。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trading_mode import normalize_trading_mode
from app.models.ai_trading.task import AITradingTask
from app.models.paper.account import PaperAccount
from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.services.admin.user_inspect_ai import (
    get_user_pnl_summary,
    list_user_ai_decisions,
    list_user_ai_tasks,
    list_user_run_logs,
)
from app.services.admin.users_svc import get_user_or_404, user_to_admin_dict
from app.services.paper.account_svc import get_account_summary
from app.services.paper.order_query import order_to_dict, position_to_dict

# 再导出，保持 admin API 只依赖本模块
__all__ = [
    "get_user_overview",
    "list_user_orders",
    "list_user_positions",
    "list_user_ai_tasks",
    "list_user_ai_decisions",
    "list_user_run_logs",
    "get_user_pnl_summary",
]


def _mode(trading_mode: str | None) -> str:
    return normalize_trading_mode(trading_mode)


async def get_user_overview(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """用户详情总览：资料 + 指定盘模拟账户 + 该盘任务统计"""
    mode = _mode(trading_mode)
    user = await get_user_or_404(session, user_id)
    account = await get_account_summary(session, user_id, mode)

    task_count_stmt = (
        select(AITradingTask.status, func.count())
        .where(
            AITradingTask.user_id == user_id,
            AITradingTask.trading_mode == mode,
        )
        .group_by(AITradingTask.status)
    )
    status_rows = (await session.execute(task_count_stmt)).all()
    task_by_status = {str(s): int(c) for s, c in status_rows}
    total_tasks = sum(task_by_status.values())

    return {
        "user": user_to_admin_dict(
            user,
            risk_rate=float(account.get("risk_rate") or 0),
            total_equity=float(account.get("total_equity") or 0),
            total_claimed=float(account.get("total_claimed") or 0),
        ),
        "trading_mode": mode,
        "paper_account": account,
        "ai_task_stats": {
            "total": total_tasks,
            "running": task_by_status.get("running", 0),
            "paused": task_by_status.get("paused", 0),
            "stopped": task_by_status.get("stopped", 0),
            "by_status": task_by_status,
        },
    }


async def list_user_orders(
    session: AsyncSession,
    user_id: UUID,
    status_filter: str | None,
    limit: int,
    offset: int,
    trading_mode: str = "live",
) -> dict:
    """用户模拟委托列表（按账户盘过滤）"""
    mode = _mode(trading_mode)
    await get_user_or_404(session, user_id)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    filters = [
        PaperOrder.user_id == user_id,
        PaperAccount.trading_mode == mode,
    ]
    if status_filter:
        filters.append(PaperOrder.status == status_filter)

    count_stmt = (
        select(func.count())
        .select_from(PaperOrder)
        .join(PaperAccount, PaperAccount.id == PaperOrder.account_id)
        .where(*filters)
    )
    list_stmt = (
        select(PaperOrder)
        .join(PaperAccount, PaperAccount.id == PaperOrder.account_id)
        .where(*filters)
        .order_by(PaperOrder.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = list((await session.execute(list_stmt)).scalars().all())
    return {
        "total": total,
        "trading_mode": mode,
        "items": [order_to_dict(o) for o in rows],
    }


async def list_user_positions(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """用户当前持仓（按账户盘过滤）"""
    mode = _mode(trading_mode)
    await get_user_or_404(session, user_id)
    stmt = (
        select(PaperPosition)
        .join(PaperAccount, PaperAccount.id == PaperPosition.account_id)
        .where(
            PaperPosition.user_id == user_id,
            PaperPosition.quantity > 0,
            PaperAccount.trading_mode == mode,
        )
        .order_by(PaperPosition.updated_at.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {
        "total": len(rows),
        "trading_mode": mode,
        "items": [position_to_dict(p) for p in rows],
    }
