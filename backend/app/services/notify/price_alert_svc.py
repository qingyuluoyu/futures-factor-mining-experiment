"""价格预警服务 —— CRUD + 后台触达检测

CRUD 按 (user_id, trading_mode) 隔离；detect_triggered 由 scheduler 周期调用，
对 enabled 预警读 Redis 最新价比对，命中则写通知并自动禁用（避免刷屏）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.trading_mode import normalize_trading_mode
from app.models.price_alert import DIRECTION_ABOVE, DIRECTION_BELOW, PriceAlert
from app.services.market_keys import get_quote_json
from app.services.notify.notify_svc import (
    create_notification,
    ensure_notify_setting,
    notification_to_dict,
)

logger = logging.getLogger("qihuo.price_alert_svc")


def price_alert_to_dict(item: PriceAlert) -> dict:
    """ORM → API 字典"""
    return {
        "id": str(item.id),
        "symbol": item.symbol,
        "contract_name": item.contract_name,
        "direction": item.direction,
        "target_price": float(item.target_price),
        "enabled": bool(item.enabled),
        "triggered_at": item.triggered_at.isoformat()
        if item.triggered_at
        else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "trading_mode": item.trading_mode,
    }


async def list_alerts(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    only_enabled: bool = False,
) -> list[dict]:
    """分盘列出用户预警（默认全部，含已触发）"""
    mode = normalize_trading_mode(trading_mode)
    stmt = select(PriceAlert).where(
        PriceAlert.user_id == user_id,
        PriceAlert.trading_mode == mode,
    )
    if only_enabled:
        stmt = stmt.where(PriceAlert.enabled.is_(True))
    stmt = stmt.order_by(PriceAlert.created_at.desc())
    result = await session.execute(stmt)
    return [price_alert_to_dict(r) for r in result.scalars().all()]


async def create_alert(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    symbol: str,
    contract_name: str,
    direction: str,
    target_price: Decimal,
) -> dict:
    """新建预警（direction/target_price 已由 schema 校验）"""
    mode = normalize_trading_mode(trading_mode)
    item = PriceAlert(
        user_id=user_id,
        trading_mode=mode,
        symbol=symbol.lower(),
        contract_name=contract_name,
        direction=direction,
        target_price=target_price,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return price_alert_to_dict(item)


async def update_alert_enabled(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    alert_id: UUID,
    enabled: bool,
) -> dict | None:
    """启停预警（重新启用时清 triggered_at）"""
    mode = normalize_trading_mode(trading_mode)
    result = await session.execute(
        select(PriceAlert).where(
            and_(
                PriceAlert.id == alert_id,
                PriceAlert.user_id == user_id,
                PriceAlert.trading_mode == mode,
            )
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        return None
    item.enabled = bool(enabled)
    if enabled:
        item.triggered_at = None
    await session.commit()
    await session.refresh(item)
    return price_alert_to_dict(item)


async def delete_alert(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    alert_id: UUID,
) -> bool:
    """删除预警"""
    mode = normalize_trading_mode(trading_mode)
    from sqlalchemy import delete

    result = await session.execute(
        delete(PriceAlert).where(
            and_(
                PriceAlert.id == alert_id,
                PriceAlert.user_id == user_id,
                PriceAlert.trading_mode == mode,
            )
        )
    )
    await session.commit()
    return result.rowcount > 0


def _is_hit(direction: str, target: Decimal, last: Decimal) -> bool:
    """方向命中：above=最新价≥目标；below=最新价≤目标"""
    if direction == DIRECTION_ABOVE:
        return last >= target
    if direction == DIRECTION_BELOW:
        return last <= target
    return False


async def detect_triggered(
    redis_conn: aioredis.Redis,
    session: AsyncSession,
) -> int:
    """扫所有 enabled 预警，命中则写通知 + 禁用。返回命中条数。

    全盘扫描（跨用户、跨 trading_mode）；按 symbol 读一次 Redis 价。
    """
    result = await session.execute(
        select(PriceAlert).where(PriceAlert.enabled.is_(True))
    )
    alerts = list(result.scalars().all())
    if not alerts:
        return 0

    # 同 (mode, symbol) 只读一次 Redis
    quote_cache: dict[tuple[str, str], float] = {}
    hit_n = 0
    for alert in alerts:
        key = (alert.trading_mode, alert.symbol)
        last = quote_cache.get(key)
        if last is None:
            q = await get_quote_json(redis_conn, alert.trading_mode, alert.symbol)
            try:
                last = float(q.get("last_price") or 0) if q else 0.0
            except (TypeError, ValueError):
                last = 0.0
            quote_cache[key] = last
        if last <= 0:
            continue
        if not _is_hit(alert.direction, Decimal(str(alert.target_price)), Decimal(str(last))):
            continue

        setting = await ensure_notify_setting(session, alert.user_id)
        verb = "涨到" if alert.direction == DIRECTION_ABOVE else "跌到"
        title = f"{alert.contract_name} 价格预警触发"
        content = (
            f"{alert.contract_name}（{alert.symbol}）最新价 {last}，"
            f"已{verb}目标价 {alert.target_price}"
        )
        created = await create_notification(
            session,
            alert.user_id,
            alert.trading_mode,
            "trade",
            title,
            content,
            respect_setting=setting,
        )
        # 命中即禁用（无论开关是否拦截通知），避免持续命中刷屏
        alert.enabled = False
        alert.triggered_at = datetime.now(timezone.utc)
        hit_n += 1
        if created is None:
            logger.info(
                "价格预警命中但被开关拦截 user=%s symbol=%s", alert.user_id, alert.symbol
            )

    if hit_n:
        await session.commit()
        logger.info("价格预警命中 %d 条", hit_n)
    return hit_n
