"""模拟交易查询与撤单"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.services.paper.account_svc import ensure_paper_account
from app.services.paper.order_fill import append_ledger, get_position, now_utc


def iso_utc(dt) -> str:
    """统一输出 UTC ISO（带 Z），避免前端按本地时区误解析 naive datetime"""
    if dt is None:
        return ""
    from datetime import timezone

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def order_to_dict(order: PaperOrder) -> dict:
    return {
        "id": str(order.id),
        "symbol": order.symbol,
        "symbol_name": order.symbol_name,
        "direction": order.direction,
        "offset": order.offset,
        "order_type": order.order_type,
        "price": float(order.price),
        "quantity": order.quantity,
        "filled_qty": order.filled_qty,
        "status": order.status,
        "source": order.source,
        "frozen_margin": float(order.frozen_margin),
        "fee": float(order.fee),
        "realized_pnl": float(order.realized_pnl),
        "multiplier": order.multiplier,
        "margin_rate": float(order.margin_rate),
        "created_at": iso_utc(order.created_at),
        "updated_at": iso_utc(order.updated_at),
        "filled_at": iso_utc(order.filled_at) if order.filled_at else None,
    }


def position_to_dict(pos: PaperPosition) -> dict:
    return {
        "id": str(pos.id),
        "symbol": pos.symbol,
        "symbol_name": pos.symbol_name,
        "direction": pos.direction,
        "source": pos.source,
        "quantity": pos.quantity,
        "available_quantity": pos.available_qty,
        "avg_price": float(pos.avg_price),
        "margin": float(pos.margin),
        "realized_pnl": float(pos.realized_pnl),
        "multiplier": pos.multiplier,
        "margin_rate": float(pos.margin_rate),
        "updated_at": iso_utc(pos.updated_at),
    }


async def cancel_order(
    session: AsyncSession,
    user_id: UUID,
    order_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """撤销 pending 委托并解冻保证金 / 恢复可平量"""
    account = await ensure_paper_account(session, user_id, trading_mode)
    stmt = select(PaperOrder).where(
        PaperOrder.id == order_id,
        PaperOrder.user_id == user_id,
        PaperOrder.account_id == account.id,
    )
    result = await session.execute(stmt)
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail="委托不存在")
    if order.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态 {order.status} 不可撤单")

    if order.offset == "open" and order.frozen_margin > 0:
        account.balance = account.balance + order.frozen_margin
        account.frozen_margin = max(
            Decimal("0"), account.frozen_margin - order.frozen_margin
        )
        account.updated_at = now_utc()
        append_ledger(
            session,
            account,
            "unfreeze",
            order.frozen_margin,
            f"撤单释放保证金 {order.symbol}",
            "order",
            order.id,
        )
        order.frozen_margin = Decimal("0")
    elif order.offset == "close":
        pos_dir = "short" if order.direction == "buy" else "long"
        pos = await get_position(
            session,
            account.id,
            order.symbol,
            pos_dir,
            order.source,
            task_id=order.task_id,
        )
        if pos is not None:
            pos.available_qty = pos.available_qty + (order.quantity - order.filled_qty)
            pos.updated_at = now_utc()

    order.status = "cancelled"
    order.updated_at = now_utc()
    await session.flush()
    return order_to_dict(order)


async def list_orders(
    session: AsyncSession,
    user_id: UUID,
    status_filter: str | None,
    limit: int,
    offset: int,
    trading_mode: str = "live",
) -> dict:
    """查询委托列表（限定当前盘账户）"""
    account = await ensure_paper_account(session, user_id, trading_mode)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)
    conditions = [
        PaperOrder.user_id == user_id,
        PaperOrder.account_id == account.id,
    ]
    if status_filter:
        conditions.append(PaperOrder.status == status_filter)

    stmt_all = select(PaperOrder).where(*conditions)
    total = len(list((await session.execute(stmt_all)).scalars().all()))

    stmt = (
        select(PaperOrder)
        .where(*conditions)
        .order_by(PaperOrder.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {"total": total, "items": [order_to_dict(r) for r in rows]}


async def list_positions(
    session: AsyncSession,
    user_id: UUID,
    trading_mode: str = "live",
) -> dict:
    """查询当前持仓（限定当前盘账户）"""
    account = await ensure_paper_account(session, user_id, trading_mode)
    stmt = (
        select(PaperPosition)
        .where(
            PaperPosition.account_id == account.id,
            PaperPosition.quantity > 0,
        )
        .order_by(PaperPosition.updated_at.desc())
    )
    rows = list((await session.execute(stmt)).scalars().all())
    items = [position_to_dict(r) for r in rows]
    total_margin = sum((r.margin for r in rows), Decimal("0"))
    return {
        "total": len(items),
        "total_margin": float(total_margin),
        "items": items,
    }
