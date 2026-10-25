"""任务预警服务 —— 设置 upsert

按 (user_id, trading_mode) 隔离，镜像 big_order_svc 模式。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trading_mode import normalize_trading_mode
from app.models.task_alert import TaskAlertSetting

logger = logging.getLogger("qihuo.task_alert_svc")


async def get_or_create_setting(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
) -> TaskAlertSetting:
    """读取用户任务预警设置；不存在则建默认行"""
    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        select(TaskAlertSetting).where(
            and_(
                TaskAlertSetting.user_id == user_id,
                TaskAlertSetting.trading_mode == mode,
            )
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = TaskAlertSetting(user_id=user_id, trading_mode=mode)
    session.add(row)
    await session.flush()
    return row


async def update_setting(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    payload: dict,
) -> TaskAlertSetting:
    """部分更新设置（仅写入 payload 中提供的字段）"""
    mode = normalize_trading_mode(trading_mode)
    row = await get_or_create_setting(session, user_id, mode)
    for key in (
        "open_enabled",
        "close_enabled",
        "popup_enabled",
        "sound_enabled",
        "voice_enabled",
    ):
        if key in payload and payload[key] is not None:
            setattr(row, key, payload[key])
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row
