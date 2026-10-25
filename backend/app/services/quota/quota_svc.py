"""活跃任务资源限制与旧日用量接口兼容；因子和回测不再限制使用次数。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.task import AITradingTask
from app.models.user import User

_BJ_TZ = ZoneInfo("Asia/Shanghai")

# 每日计数 feature 名
FEATURE_FACTOR = "factor"
FEATURE_BACKTEST = "backtest"


def _today_key() -> str:
    """上海时区当日日期键 yyyymmdd（次日自动变更 → 计数归零）"""
    return datetime.now(_BJ_TZ).strftime("%Y%m%d")


def _quota_key(user_id: UUID, feature: str) -> str:
    """redis 配额计数 key"""
    return f"quota:{user_id}:{feature}:{_today_key()}"


def _resolve_limit(override: int | None, default: int) -> int | None:
    """合并 override 与默认；0 表示不限（返回 None）"""
    raw = int(override) if override is not None else int(default)
    return None if raw <= 0 else raw


async def get_effective_limits(
    session: AsyncSession,
    user: User,
) -> dict[str, int | None]:
    """返回用户生效配额 {max_tasks, factor_daily, backtest_daily}；None=不限"""
    if str(getattr(user, "role", "user")) == "admin":
        return {"max_tasks": None, "factor_daily": None, "backtest_daily": None}
    from app.services.system_settings_svc import ensure_system_settings

    settings = await ensure_system_settings(session)
    return {
        "max_tasks": _resolve_limit(
            getattr(user, "max_tasks_override", None),
            int(getattr(settings, "max_tasks_per_user", 5)),
        ),
        "factor_daily": None,
        "backtest_daily": None,
    }


async def count_active_tasks(session: AsyncSession, user: User) -> int:
    """用户活跃任务数（running + paused）"""
    stmt = select(func.count(AITradingTask.id)).where(
        AITradingTask.user_id == user.id,
        AITradingTask.status.in_(("running", "paused")),
    )
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def daily_used(
    redis_conn: Any,
    user_id: UUID,
    feature: str,
) -> int:
    """用户今日某功能已用次数"""
    if redis_conn is None:
        return 0
    raw = await redis_conn.get(_quota_key(user_id, feature))
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def check_and_incr(
    redis_conn: Any,
    user: User,
    feature: str,
    limit: int | None,
    label: str,
) -> int:
    """兼容旧调用签名；不读取或消耗日配额，既有Redis记录保留供审计。"""
    if feature not in (FEATURE_FACTOR, FEATURE_BACKTEST):
        raise ValueError("未知业务用量类别")
    return 0
