"""已挂限价单的成交执行 —— 开仓/平仓资金与持仓变更

开仓挂单的保证金已在下单时冻结，成交时只扣手续费。
平仓挂单的可平量已在下单时占用，成交时不再重复扣可平量。
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper.account import PaperAccount
from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.services.notify.trade_notify import notify_order_filled
from app.services.paper.order_fill import (
    append_ledger,
    calc_fee,
    calc_margin,
    get_position,
    now_utc,
)

logger = logging.getLogger("qihuo.paper.pending_fill")


async def fill_pending_open(
    session: AsyncSession,
    account: PaperAccount,
    order: PaperOrder,
    fill_price: Decimal,
    fee_mode: str,
    fee_value: Decimal,
) -> None:
    """成交已冻结保证金的开仓挂单"""
    fill_qty = order.quantity - order.filled_qty
    if fill_qty <= 0:
        return
    fee = calc_fee(fill_price, fill_qty, order.multiplier, fee_mode, fee_value)
    margin = order.frozen_margin
    if margin <= 0:
        margin = calc_margin(
            fill_price, fill_qty, order.multiplier, order.margin_rate
        )

    if account.balance < fee:
        logger.warning(
            "挂单 %s 手续费不足，跳过 user=%s", order.id, account.user_id
        )
        return

    account.balance = account.balance - fee
    account.updated_at = now_utc()

    pos_dir = "long" if order.direction == "buy" else "short"
    pos = await get_position(
        session,
        account.id,
        order.symbol,
        pos_dir,
        order.source,
        task_id=order.task_id,
    )
    if pos is None:
        session.add(
            PaperPosition(
                account_id=account.id,
                user_id=account.user_id,
                symbol=order.symbol,
                symbol_name=order.symbol_name,
                direction=pos_dir,
                source=order.source,
                quantity=fill_qty,
                available_qty=fill_qty,
                avg_price=fill_price,
                margin=margin,
                realized_pnl=Decimal("0"),
                multiplier=order.multiplier,
                margin_rate=order.margin_rate,
                task_id=order.task_id,
                created_at=now_utc(),
                updated_at=now_utc(),
            )
        )
    else:
        total_qty = pos.quantity + fill_qty
        pos.avg_price = (
            (pos.avg_price * Decimal(pos.quantity) + fill_price * Decimal(fill_qty))
            / Decimal(total_qty)
        ).quantize(Decimal("0.0001"))
        pos.quantity = total_qty
        pos.available_qty = pos.available_qty + fill_qty
        pos.margin = pos.margin + margin
        pos.updated_at = now_utc()

    order.filled_qty = fill_qty
    order.status = "filled"
    order.fee = fee
    order.filled_at = now_utc()
    order.updated_at = now_utc()

    append_ledger(
        session,
        account,
        "fee",
        -fee,
        f"挂单成交手续费 {order.symbol} {fill_qty}手",
        "order",
        order.id,
    )

    await notify_order_filled(
        session,
        account.user_id,
        account.trading_mode,
        order.symbol,
        order.symbol_name,
        order.direction,
        fill_price,
        fill_qty,
        "open",
        task_id=order.task_id,
        order_id=order.id,
    )


async def fill_pending_close(
    session: AsyncSession,
    account: PaperAccount,
    order: PaperOrder,
    fill_price: Decimal,
    fee_mode: str,
    fee_value: Decimal,
) -> None:
    """成交已占用可平量的平仓挂单"""
    fill_qty = order.quantity - order.filled_qty
    if fill_qty <= 0:
        return

    pos_dir = "short" if order.direction == "buy" else "long"
    pos = await get_position(
        session,
        account.id,
        order.symbol,
        pos_dir,
        order.source,
        task_id=order.task_id,
    )
    if pos is None or pos.quantity < fill_qty:
        logger.warning(
            "挂单 %s 持仓不足，跳过 symbol=%s", order.id, order.symbol
        )
        return

    fee = calc_fee(fill_price, fill_qty, order.multiplier, fee_mode, fee_value)
    if pos_dir == "long":
        pnl = (fill_price - pos.avg_price) * Decimal(fill_qty) * Decimal(pos.multiplier)
    else:
        pnl = (pos.avg_price - fill_price) * Decimal(fill_qty) * Decimal(pos.multiplier)
    pnl = pnl.quantize(Decimal("0.01"))

    release_margin = Decimal("0")
    if pos.quantity > 0:
        release_margin = (
            pos.margin * Decimal(fill_qty) / Decimal(pos.quantity)
        ).quantize(Decimal("0.01"))

    if account.balance + release_margin + pnl < fee:
        logger.warning("挂单 %s 平仓资金不足手续费，跳过", order.id)
        return

    account.balance = account.balance + release_margin + pnl - fee
    account.frozen_margin = max(Decimal("0"), account.frozen_margin - release_margin)
    account.realized_pnl = account.realized_pnl + pnl
    account.updated_at = now_utc()

    pos.quantity = pos.quantity - fill_qty
    pos.margin = max(Decimal("0"), pos.margin - release_margin)
    pos.realized_pnl = pos.realized_pnl + pnl
    pos.updated_at = now_utc()
    if pos.quantity <= 0:
        await session.delete(pos)

    order.filled_qty = fill_qty
    order.status = "filled"
    order.fee = fee
    order.realized_pnl = pnl
    order.filled_at = now_utc()
    order.updated_at = now_utc()

    append_ledger(
        session, account, "unfreeze", release_margin,
        f"挂单平仓释放 {order.symbol} {fill_qty}手", "order", order.id,
    )
    append_ledger(
        session, account, "close", pnl,
        f"挂单平仓盈亏 {order.symbol} {fill_qty}手", "order", order.id,
    )
    append_ledger(
        session, account, "fee", -fee,
        f"挂单平仓手续费 {order.symbol} {fill_qty}手", "order", order.id,
    )

    await notify_order_filled(
        session,
        account.user_id,
        account.trading_mode,
        order.symbol,
        order.symbol_name,
        order.direction,
        fill_price,
        fill_qty,
        "close",
        task_id=order.task_id,
        order_id=order.id,
    )
