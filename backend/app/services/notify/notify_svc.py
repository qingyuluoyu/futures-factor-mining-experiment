"""通知服务 —— 站内消息的生成 / 查询 / 已读 / 删除

纯函数 + AsyncSession，不持有连接。调用方负责 commit 时机：
- 查询/已读/删除：内部 commit
- create_notification：仅 add + flush（不独立 commit），便于挂钩方并入同一事务
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trading_mode import normalize_trading_mode
from app.models.notification import (
    CATEGORY_FUND,
    CATEGORY_RISK,
    CATEGORY_SYSTEM,
    CATEGORY_TRADE,
    Notification,
)
from app.models.user_notify_setting import UserNotifySetting

logger = logging.getLogger("qihuo.notify_svc")

# 类别 → 开关字段映射
_CATEGORY_TO_FLAG: dict[str, str] = {
    CATEGORY_TRADE: "notify_trade",
    CATEGORY_FUND: "notify_fund",
    # system / risk 默认总开（不受三档开关控制）
    CATEGORY_SYSTEM: "",
    CATEGORY_RISK: "",
}


async def ensure_notify_setting(
    session: AsyncSession,
    user_id: UUID,
) -> UserNotifySetting:
    """读取用户通知开关；不存在则建默认行（全开）"""
    result = await session.execute(
        select(UserNotifySetting).where(UserNotifySetting.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = UserNotifySetting(user_id=user_id)
    session.add(row)
    await session.flush()
    return row


def is_category_enabled(setting: UserNotifySetting, category: str) -> bool:
    """该类别是否允许生成通知（system/risk 不受开关限制）"""
    flag = _CATEGORY_TO_FLAG.get(category, "")
    if not flag:
        return True
    return bool(getattr(setting, flag, True))


async def create_notification(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    category: str,
    title: str,
    content: str,
    respect_setting: UserNotifySetting | None = None,
) -> Notification | None:
    """生成一条通知。

    respect_setting 非空时按开关决定是否生成；返回 None 表示被开关拦截。
    仅 add + flush，不 commit（由调用方事务控制）。
    flush 成功后通过 WebSocket 实时推送给用户。
    """
    if respect_setting is not None and not is_category_enabled(respect_setting, category):
        return None
    mode = normalize_trading_mode(trading_mode)
    item = Notification(
        user_id=user_id,
        trading_mode=mode,
        category=category,
        title=title,
        content=content,
    )
    session.add(item)
    await session.flush()
    # 实时推送给用户的 WS 连接（id 已生成，前端收到即可弹窗）
    try:
        from app.api.ws import broadcast_notification

        broadcast_notification(str(user_id), notification_to_dict(item))
    except Exception:
        pass
    return item


def notification_to_dict(item: Notification) -> dict:
    """ORM → API 字典（对齐前端 Message 结构）"""
    return {
        "id": str(item.id),
        "category": item.category,
        "title": item.title,
        "content": item.content,
        "read": bool(item.read),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "trading_mode": item.trading_mode,
    }


async def list_notifications(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    category: str = "",
    read_filter: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """分盘查询通知，可按类别/已读过滤；按 created_at 倒序"""
    mode = normalize_trading_mode(trading_mode)
    stmt = select(Notification).where(
        Notification.user_id == user_id,
        Notification.trading_mode == mode,
    )
    if category:
        stmt = stmt.where(Notification.category == category)
    if read_filter == "unread":
        stmt = stmt.where(Notification.read.is_(False))
    elif read_filter == "read":
        stmt = stmt.where(Notification.read.is_(True))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return [notification_to_dict(r) for r in result.scalars().all()]


async def unread_count(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
) -> int:
    """未读条数（分盘）"""
    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        select(func.count(Notification.id)).where(
            and_(
                Notification.user_id == user_id,
                Notification.trading_mode == mode,
                Notification.read.is_(False),
            )
        )
    )
    return int(result.scalar_one() or 0)


async def mark_read(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    notify_id: UUID,
) -> bool:
    """标记单条已读（校验归属）；返回是否命中"""
    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        update(Notification)
        .where(
            and_(
                Notification.id == notify_id,
                Notification.user_id == user_id,
                Notification.trading_mode == mode,
            )
        )
        .values(read=True)
    )
    await session.commit()
    # 推送未读数更新
    try:
        from app.api.ws import broadcast_unread_count

        cnt = await unread_count(session, user_id, trading_mode)
        broadcast_unread_count(str(user_id), cnt)
    except Exception:
        pass
    return result.rowcount > 0


async def mark_all_read(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
) -> int:
    """当前盘全部已读；返回更新条数"""
    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        update(Notification)
        .where(
            and_(
                Notification.user_id == user_id,
                Notification.trading_mode == mode,
                Notification.read.is_(False),
            )
        )
        .values(read=True)
    )
    await session.commit()
    # 推送未读数更新
    try:
        from app.api.ws import broadcast_unread_count

        cnt = await unread_count(session, user_id, trading_mode)
        broadcast_unread_count(str(user_id), cnt)
    except Exception:
        pass
    return int(result.rowcount or 0)


async def delete_notification(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    notify_id: UUID,
) -> bool:
    """删除单条（校验归属）"""
    mode = normalize_trading_mode(trading_mode)
    from sqlalchemy import delete

    result = await session.execute(
        delete(Notification).where(
            and_(
                Notification.id == notify_id,
                Notification.user_id == user_id,
                Notification.trading_mode == mode,
            )
        )
    )
    await session.commit()
    return result.rowcount > 0
