"""注册策略校验 —— 默认开放，显式 managed 模式使用数据库开关与日限额"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.user import User
from app.services.system_settings_svc import (
    current_register_window_start,
    ensure_system_settings,
)


def _window_start_utc(reset_hour: int) -> datetime:
    """注册窗口起点转 UTC（naive 比较时用 aware）"""
    local_start = current_register_window_start(reset_hour)
    return local_start.astimezone(timezone.utc)


async def get_public_registration_status(
    session: AsyncSession,
    app_settings: Settings,
) -> dict:
    """公开注册状态（无需登录）"""
    settings = await ensure_system_settings(session)
    window_start = _window_start_utc(settings.register_reset_hour)

    count_stmt = select(func.count()).select_from(User).where(
        User.created_at >= window_start
    )
    registered_in_window = int((await session.execute(count_stmt)).scalar_one())

    managed = app_settings.registration_policy == "managed"
    enabled = bool(settings.registration_enabled) if managed else True
    limit = int(settings.daily_register_limit) if managed else 0
    remaining = None
    if limit > 0:
        remaining = max(0, limit - registered_in_window)

    can_register = enabled
    reason = ""
    if not enabled:
        can_register = False
        reason = "系统已关闭注册"
    elif limit > 0 and registered_in_window >= limit:
        can_register = False
        reason = f"今日注册名额已满（{limit} 人），请 {settings.register_reset_hour:02d}:00 后重试"

    return {
        "registration_policy": app_settings.registration_policy,
        "registration_enabled": enabled,
        "daily_register_limit": limit,
        "configured_registration_enabled": bool(settings.registration_enabled),
        "configured_daily_register_limit": int(settings.daily_register_limit),
        "register_reset_hour": int(settings.register_reset_hour),
        "registered_in_window": registered_in_window,
        "remaining": remaining,
        "can_register": can_register,
        "reason": reason,
        "system_name": settings.system_name,
        "window_start": window_start.isoformat().replace("+00:00", "Z"),
    }


async def assert_can_register(session: AsyncSession, app_settings: Settings) -> None:
    """注册前硬校验，不通过则抛 403"""
    if app_settings.registration_policy == "open":
        return
    status_info = await get_public_registration_status(session, app_settings)
    if not status_info["can_register"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=status_info["reason"] or "当前不可注册",
        )
