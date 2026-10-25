"""大单预警服务 —— 设置 upsert

按 (user_id, trading_mode) 隔离。命中记录相关函数已移除（改用 trade_tick 全量存储）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trading_mode import normalize_trading_mode
from app.models.big_order import BigOrderAlertSetting

logger = logging.getLogger("qihuo.big_order_svc")


async def get_or_create_setting(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
) -> BigOrderAlertSetting:
    """读取用户大单设置；不存在则建默认行"""
    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        select(BigOrderAlertSetting).where(
            and_(
                BigOrderAlertSetting.user_id == user_id,
                BigOrderAlertSetting.trading_mode == mode,
            )
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = BigOrderAlertSetting(user_id=user_id, trading_mode=mode)
    session.add(row)
    await session.flush()
    return row


async def update_setting(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    payload: dict,
) -> BigOrderAlertSetting:
    """部分更新设置（仅写入 payload 中提供的字段）"""
    mode = normalize_trading_mode(trading_mode)
    row = await get_or_create_setting(session, user_id, mode)
    for key in (
        "buy_enabled",
        "buy_threshold",
        "buy_color",
        "sell_enabled",
        "sell_threshold",
        "sell_color",
        "popup_enabled",
        "sound_enabled",
        "voice_enabled",
    ):
        if key in payload and payload[key] is not None:
            setattr(row, key, payload[key])
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row
