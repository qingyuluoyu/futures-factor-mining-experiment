"""管理仪表盘聚合 —— 渠道/系统/AI/用户资金四块摘要，供 /admin/dashboard 一次拉取"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_trading.task import AITradingTask
from app.models.paper.account import PaperAccount
from app.models.user import User

HEALTH_KEY_PREFIX = "market:channel:health:"
ACTIVE_CHANNEL_KEY = "market:live:active_channel"

_BJ_TZ = timezone(timedelta(hours=8))

# 系统健康：关注的容器（status=running/Exited 等）
_WATCH_CONTAINERS = (
    "qihuo-backend",
    "qihuo-frontend",
    "qihuo-ctp-bridge",
    "qihuo-ctp-bridge-virtual",
)


async def _get_str(redis_conn: Any, key: str) -> str | None:
    raw = await redis_conn.get(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


async def collect_channels_summary(redis_conn: Any) -> dict[str, Any]:
    """渠道摘要：当前生效渠道 + 各渠道健康（红绿橙）"""
    active = await _get_str(redis_conn, ACTIVE_CHANNEL_KEY) or "sina"
    channels: dict[str, dict] = {}
    async for key in redis_conn.scan_iter(f"{HEALTH_KEY_PREFIX}*"):
        name = key.decode() if isinstance(key, bytes) else str(key)
        ch = name.rsplit(":", 1)[-1]
        try:
            raw = await redis_conn.get(key)
            channels[ch] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError):
            channels[ch] = {}
    return {"active": active, "channels": channels}


async def collect_system_health(
    session: AsyncSession, redis_conn: Any
) -> dict[str, Any]:
    """系统服务健康：容器状态 + Redis / PostgreSQL 存活"""
    containers: dict[str, str] = {}
    try:
        import aiodocker  # type: ignore

        async with aiodocker.Docker() as docker:
            for name in _WATCH_CONTAINERS:
                try:
                    info = await docker.containers.container(name).show()
                    containers[name] = info.get("State", {}).get(
                        "Status", "unknown"
                    )
                except Exception:
                    containers[name] = "missing"
    except Exception:
        # docker.sock 不可用：仅返回 redis/pg 健康状态
        containers = {name: "unknown" for name in _WATCH_CONTAINERS}

    redis_ok = False
    try:
        redis_ok = bool(await redis_conn.ping())
    except Exception:
        redis_ok = False

    pg_ok = False
    try:
        await session.execute(select(1))
        pg_ok = True
    except Exception:
        pg_ok = False

    return {"containers": containers, "redis": redis_ok, "postgres": pg_ok}


async def collect_ai_summary(session: AsyncSession) -> dict[str, Any]:
    """AI 交易总览：各盘各状态任务数 + 运行中分配资金"""
    rows = (
        await session.execute(
            select(
                AITradingTask.trading_mode,
                AITradingTask.status,
                func.count().label("n"),
                func.coalesce(
                    func.sum(AITradingTask.allocated_capital), 0
                ).label("cap"),
            ).group_by(AITradingTask.trading_mode, AITradingTask.status)
        )
    ).all()
    by_mode: dict[str, Any] = {}
    running = 0
    total = 0
    allocated = Decimal("0")
    for row in rows:
        mode, status, n, cap = row[0], row[1], int(row[2]), Decimal(str(row[3]))
        by_mode.setdefault(mode, {})[status] = {"count": n, "capital": float(cap)}
        total += n
        if status == "running":
            running += n
            allocated += cap
    return {
        "by_mode": by_mode,
        "running": running,
        "total": total,
        "allocated_capital": float(allocated),
    }


async def collect_user_finance_summary(session: AsyncSession) -> dict[str, Any]:
    """用户与资金：总数/今日新增/各盘账户数与总权益"""
    total_users = int(
        (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()
    )
    today_start = (
        datetime.now(_BJ_TZ)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    today_new = int(
        (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.created_at >= today_start)
            )
        ).scalar_one()
    )
    fin_rows = (
        await session.execute(
            select(
                PaperAccount.trading_mode,
                func.count().label("n"),
                func.coalesce(
                    func.sum(PaperAccount.balance + PaperAccount.realized_pnl),
                    0,
                ).label("equity"),
            ).group_by(PaperAccount.trading_mode)
        )
    ).all()
    by_mode: dict[str, Any] = {}
    total_equity = Decimal("0")
    for row in fin_rows:
        mode, n, eq = row[0], int(row[1]), Decimal(str(row[2]))
        by_mode[mode] = {"accounts": n, "equity": float(eq)}
        total_equity += eq
    return {
        "total_users": total_users,
        "today_new": today_new,
        "by_mode": by_mode,
        "total_equity": float(total_equity),
    }


async def collect_admin_overview(
    session: AsyncSession,
    redis_conn: Any,
) -> dict[str, Any]:
    """聚合仪表盘四块：渠道 / 系统 / AI / 用户资金"""
    return {
        "channels": await collect_channels_summary(redis_conn),
        "system": await collect_system_health(session, redis_conn),
        "ai": await collect_ai_summary(session),
        "users": await collect_user_finance_summary(session),
    }
