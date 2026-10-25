"""模拟成交处理 —— 开仓 / 平仓资金与持仓变更"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper.account import PaperAccount
from app.models.paper.ledger import PaperLedger
from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.services.notify.trade_notify import notify_order_filled


def now_utc() -> datetime:
    """当前 UTC 时间"""
    return datetime.now(timezone.utc)


def calc_margin(
    price: Decimal,
    qty: int,
    multiplier: int,
    rate: Decimal,
) -> Decimal:
    """保证金 = 价格 × 手数 × 乘数 × 保证金率"""
    return (price * Decimal(qty) * Decimal(multiplier) * rate).quantize(Decimal("0.01"))


def calc_fee(
    price: Decimal,
    qty: int,
    multiplier: int,
    fee_mode: str,
    fee_value: Decimal,
) -> Decimal:
    """计算手续费

    fee_mode=rate  : 成交金额 × fee_value
    fee_mode=fixed : 手数 × fee_value（元/手）
    """
    if fee_mode == "fixed":
        return (Decimal(qty) * fee_value).quantize(Decimal("0.01"))
    notional = price * Decimal(qty) * Decimal(multiplier)
    return (notional * fee_value).quantize(Decimal("0.01"))


def append_ledger(
    session: AsyncSession,
    account: PaperAccount,
    flow_type: str,
    amount: Decimal,
    description: str,
    ref_type: str | None,
    ref_id: UUID | None,
) -> None:
    """追加资金流水"""
    session.add(
        PaperLedger(
            account_id=account.id,
            user_id=account.user_id,
            flow_type=flow_type,
            amount=amount,
            balance_after=account.balance,
            description=description,
            ref_type=ref_type,
            ref_id=ref_id,
            created_at=now_utc(),
        )
    )


async def get_position(
    session: AsyncSession,
    account_id: UUID,
    symbol: str,
    direction: str,
    source: str = "manual",
    task_id: UUID | None = None,
) -> PaperPosition | None:
    """按账户+合约+方向+来源+任务 查询持仓

    task_id 非空时定位任务级持仓；为空（手动单）时匹配 task_id IS NULL
    的聚合持仓行。
    """
    stmt = select(PaperPosition).where(
        PaperPosition.account_id == account_id,
        PaperPosition.symbol == symbol,
        PaperPosition.direction == direction,
        PaperPosition.source == source,
    )
    if task_id is not None:
        stmt = stmt.where(PaperPosition.task_id == task_id)
    else:
        stmt = stmt.where(PaperPosition.task_id.is_(None))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def apply_open(
    session: AsyncSession,
    account: PaperAccount,
    order: PaperOrder,
    fill_price: Decimal,
    fill_qty: int,
    fee_mode: str,
    fee_value: Decimal,
    task_id: UUID | None = None,
) -> None:
    """开仓成交：更新持仓与保证金"""
    pos_dir = "long" if order.direction == "buy" else "short"
    pos = await get_position(
        session, account.id, order.symbol, pos_dir, order.source, task_id=task_id
    )
    fee = calc_fee(fill_price, fill_qty, order.multiplier, fee_mode, fee_value)
    margin = calc_margin(fill_price, fill_qty, order.multiplier, order.margin_rate)

    if account.balance < fee + margin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="可用资金不足（含手续费）",
        )

    account.balance = account.balance - fee - margin
    account.frozen_margin = account.frozen_margin + margin
    account.updated_at = now_utc()

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
                task_id=task_id,
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
    order.frozen_margin = margin
    order.filled_at = now_utc()
    order.updated_at = now_utc()

    append_ledger(
        session,
        account,
        "fee",
        -fee,
        f"开仓手续费 {order.symbol} {fill_qty}手",
        "order",
        order.id,
    )
    append_ledger(
        session,
        account,
        "freeze",
        -margin,
        f"开仓冻结保证金 {order.symbol} {fill_qty}手",
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
        task_id=task_id,
        order_id=order.id,
    )


async def apply_close(
    session: AsyncSession,
    account: PaperAccount,
    order: PaperOrder,
    fill_price: Decimal,
    fill_qty: int,
    fee_mode: str,
    fee_value: Decimal,
    task_id: UUID | None = None,
) -> None:
    """平仓成交：释放保证金、结算盈亏"""
    pos_dir = "short" if order.direction == "buy" else "long"
    pos = await get_position(
        session, account.id, order.symbol, pos_dir, order.source, task_id=task_id
    )
    if pos is None or pos.available_qty < fill_qty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="可平手数不足",
        )

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="资金不足以支付平仓手续费",
        )

    account.balance = account.balance + release_margin + pnl - fee
    account.frozen_margin = max(Decimal("0"), account.frozen_margin - release_margin)
    account.realized_pnl = account.realized_pnl + pnl
    account.updated_at = now_utc()

    pos.quantity = pos.quantity - fill_qty
    pos.available_qty = pos.available_qty - fill_qty
    pos.margin = max(Decimal("0"), pos.margin - release_margin)
    pos.realized_pnl = pos.realized_pnl + pnl
    pos.updated_at = now_utc()
    if pos.quantity <= 0:
        await session.delete(pos)

    order.filled_qty = fill_qty
    order.status = "filled"
    order.fee = fee
    order.realized_pnl = pnl
    order.frozen_margin = Decimal("0")
    order.filled_at = now_utc()
    order.updated_at = now_utc()

    append_ledger(
        session,
        account,
        "unfreeze",
        release_margin,
        f"平仓释放保证金 {order.symbol} {fill_qty}手",
        "order",
        order.id,
    )
    append_ledger(
        session,
        account,
        "close",
        pnl,
        f"平仓盈亏 {order.symbol} {fill_qty}手",
        "order",
        order.id,
    )
    append_ledger(
        session,
        account,
        "fee",
        -fee,
        f"平仓手续费 {order.symbol} {fill_qty}手",
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
        "close",
        task_id=task_id,
        order_id=order.id,
    )
