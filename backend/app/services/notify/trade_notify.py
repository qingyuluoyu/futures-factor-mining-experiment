"""成交 / 资金 通知挂钩薄封装

避免 order_fill / account_svc 多处重复；统一受用户通知开关控制。
仅 add + flush，不独立 commit，并入调用方事务。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.notify.notify_svc import (
    create_notification,
    ensure_notify_setting,
)

logger = logging.getLogger("qihuo.trade_notify")


def publish_task_order_filled(
    user_id: UUID,
    task_id: UUID,
    order_id: UUID | None,
    trading_mode: str,
    symbol: str,
    symbol_name: str,
    direction: str,
    offset: str,
    fill_price: Decimal,
    fill_qty: int,
) -> None:
    """任务单成交后经 WS 推送 task_order_tick 事件（供 AI 看盘任务预警）。

    纯推送不入库；尽力而为，任何失败不得影响成交主流程。
    """
    from app.api.ws import broadcast_task_order

    try:
        broadcast_task_order(
            str(user_id),
            {
                "task_id": str(task_id),
                "order_id": str(order_id) if order_id else None,
                "trading_mode": str(trading_mode or "live"),
                "symbol": symbol,
                "symbol_name": symbol_name,
                "direction": direction,
                "offset": offset,
                "price": float(fill_price),
                "filled_qty": int(fill_qty),
                "time": now_bj_iso(),
            },
        )
    except Exception:
        logger.exception("任务成交流水推送失败 %s", symbol)


def now_bj_iso() -> str:
    """当前北京时间 ISO 字符串（前端 new Date() 可直接解析）"""
    from datetime import datetime, timezone, timedelta

    return datetime.now(timezone(timedelta(hours=8))).isoformat()


async def notify_order_filled(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    symbol: str,
    symbol_name: str,
    direction: str,
    fill_price: Decimal,
    fill_qty: int,
    action: str,
    task_id: UUID | None = None,
    order_id: UUID | None = None,
) -> None:
    """成交后写 trade 类通知；任务单同时推送 task_order_tick 预警事件。

    action: "open"（开仓）/ "close"（平仓）
    direction: "buy" / "sell"
    """
    try:
        setting = await ensure_notify_setting(session, user_id)
        dir_label = "买入" if direction == "buy" else "卖出"
        action_label = "开仓" if action == "open" else "平仓"
        title = f"订单成交 · {symbol_name}"
        content = (
            f"{action_label}{dir_label} {fill_qty}手 {symbol} "
            f"@ {fill_price}"
        )
        await create_notification(
            session,
            user_id,
            trading_mode,
            "trade",
            title,
            content,
            respect_setting=setting,
        )
    except Exception:
        # 通知失败不得阻断成交主流程
        logger.exception("成交通知写入失败 %s", symbol)
    if task_id is not None:
        try:
            publish_task_order_filled(
                user_id,
                task_id,
                order_id,
                trading_mode,
                symbol,
                symbol_name,
                direction,
                action,
                fill_price,
                fill_qty,
            )
        except Exception:
            logger.exception("任务成交事件推送失败 %s", symbol)


async def notify_fund_change(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str,
    amount: Decimal,
    reason: str,
) -> None:
    """资金变动（领取 / 出入金）写 fund 类通知"""
    try:
        setting = await ensure_notify_setting(session, user_id)
        title = "资金变动"
        sign = "+" if amount >= 0 else ""
        content = f"{reason}：{sign}{amount}"
        await create_notification(
            session,
            user_id,
            trading_mode,
            "fund",
            title,
            content,
            respect_setting=setting,
        )
    except Exception:
        logger.exception("资金通知写入失败")
