"""AI 交易 paper 下单执行与持仓查找（跟随任务 trading_mode）

开仓资金从 AI 资金仓（划转冻结）拨付，流水记 ai_fund_trade，
不占用创建任务后主账户剩余可用。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.models.ai_trading.task import AITradingTask
from app.models.paper.position import PaperPosition
from app.services.ai_trading.core.capital import close_task_trade, fund_task_trade
from app.services.ai_trading.task.position_pnl import source_for_task
from app.services.paper.specs import resolve_trade_params
from app.services.paper.trading_svc import place_order

logger = logging.getLogger("qihuo.ai_trading")


def _task_mode(task: AITradingTask) -> str:
    return str(getattr(task, "trading_mode", None) or "live")


def _estimate_open_need(symbol: str, price: float, quantity: int) -> Decimal:
    """估算开仓所需：保证金 + 手续费（略留余量）"""
    params = resolve_trade_params(symbol, None)
    mult = int(params.get("multiplier") or 10)
    rate = float(params.get("margin_rate") or 0.1)
    fee_mode = str(params.get("fee_mode") or "rate")
    open_fee = float(params.get("open_fee") or 0)
    px = Decimal(str(price))
    qty = Decimal(str(max(1, quantity)))
    margin = (px * qty * Decimal(mult) * Decimal(str(rate))).quantize(Decimal("0.01"))
    if fee_mode == "fixed":
        fee = (qty * Decimal(str(open_fee))).quantize(Decimal("0.01"))
    else:
        fee = (px * qty * Decimal(mult) * Decimal(str(open_fee))).quantize(
            Decimal("0.01")
        )
    # 2% 缓冲，避免费率/精度差导致二次失败
    need = (margin + fee) * Decimal("1.02")
    return need.quantize(Decimal("0.01"))


async def find_symbol_position(
    session: AsyncSession,
    user_id: UUID,
    symbol: str,
    trading_mode: str,
    source: str,
    task_id: UUID | None = None,
) -> dict[str, Any] | None:
    """查找用户在指定合约、指定任务上的持仓（按 task_id 隔离）

    优先按 task_id 直接查 DB（任务级隔离）；未传 task_id 时回退到 source 内存过滤，
    兼容历史调用方。
    """
    sym = (symbol or "").strip().lower()
    if task_id is not None:
        # 直接按 task_id 查 DB，同品种不同任务的持仓严格区分
        stmt = select(PaperPosition).where(
            PaperPosition.user_id == user_id,
            PaperPosition.task_id == task_id,
            PaperPosition.symbol == sym,
            PaperPosition.quantity > 0,
        )
        pos = (await session.execute(stmt)).scalar_one_or_none()
        if pos is None:
            return None
        return {
            "direction": pos.direction,
            "quantity": int(pos.quantity or 0),
            # ORM 属性名是 available_qty（与 order_query.position_to_dict 一致）；
            # dict key 用 available_quantity 以对齐下游 engine/risk/策略的读取
            "available_quantity": int(pos.available_qty or pos.quantity or 0),
            "avg_price": float(pos.avg_price or 0),
            "margin": float(pos.margin or 0),
            "source": pos.source,
        }
    # 回退：按 trading_mode + source 内存过滤
    from app.services.paper.order_query import list_positions

    data = await list_positions(session, user_id, trading_mode)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    for pos in items:
        if (
            str(pos.get("symbol") or "").lower() == sym
            and str(pos.get("source") or "") == source
            and int(pos.get("quantity") or 0) > 0
        ):
            return pos
    return None


async def execute_action(
    session: AsyncSession,
    task: AITradingTask,
    action: str,
    quantity: int,
    last_price: float,
    redis_conn: object | None,
) -> tuple[UUID | None, str]:
    """执行 open/close，返回 order_id 与说明"""
    if action == "hold" or quantity <= 0:
        return None, "无需下单"
    if last_price <= 0:
        return None, "无有效最新价"

    mode = _task_mode(task)
    source = source_for_task(task)
    position = await find_symbol_position(
        session, task.user_id, task.symbol, mode, source, task_id=task.id
    )
    try:
        if action in ("open_long", "open_short"):
            # 有资金仓（allocated>0）→ 从资金仓拨付；无资金仓 → 直接用主账户
            need = _estimate_open_need(task.symbol, last_price, quantity)
            if Decimal(str(getattr(task, "capital_balance", 0) or 0)) > 0:
                await fund_task_trade(session, task, need)
            direction = "buy" if action == "open_long" else "sell"
            order = await place_order(
                session,
                task.user_id,
                task.symbol,
                direction,
                "open",
                "market",
                last_price,
                quantity,
                task.symbol_name,
                None,
                None,
                redis_conn,
                mode,
                source,
                task_id=task.id,
            )
            side = "开多" if action == "open_long" else "开空"
            return UUID(order["id"]), f"{side} {quantity} 手（AI资金仓）"
        if action == "close":
            if not position:
                return None, "无持仓"
            pos_dir = str(position.get("direction") or "")
            qty = min(
                quantity,
                int(
                    position.get("available_quantity")
                    or position.get("quantity")
                    or 0
                ),
            )
            if qty <= 0:
                return None, "可平手数为 0"
            direction = "sell" if pos_dir == "long" else "buy"
            order = await place_order(
                session,
                task.user_id,
                task.symbol,
                direction,
                "close",
                "market",
                last_price,
                qty,
                task.symbol_name,
                None,
                None,
                redis_conn,
                mode,
                source,
                task_id=task.id,
            )
            # 平仓回款记入任务资金仓（释放保证金 + 盈亏 - 手续费）
            pos_margin = Decimal(str(position.get("margin") or 0))
            pos_qty = max(1, int(position.get("quantity") or 1))
            release_margin = (
                pos_margin * Decimal(qty) / Decimal(pos_qty)
            ).quantize(Decimal("0.01"))
            realized = Decimal(str(order.get("realized_pnl") or 0))
            close_fee = Decimal(str(order.get("fee") or 0))
            back = (release_margin + realized - close_fee).quantize(
                Decimal("0.01")
            )
            # 有资金仓 → 平仓回款记入；无资金仓 → 跳过（主账户直接处理）
            if Decimal(str(getattr(task, "capital_balance", 0) or 0)) > 0:
                await close_task_trade(session, task, back)
            return UUID(order["id"]), f"平仓 {qty} 手"
    except Exception as exc:
        logger.warning("AI 任务下单失败 task=%s: %s", task.id, exc)
        return None, f"下单失败: {exc}"
    return None, f"未知动作 {action}"
