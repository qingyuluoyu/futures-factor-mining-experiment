"""模拟交易服务 —— 下单"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.paper.order import PaperOrder
from app.data.instrument_scope import ensure_cny_futures_symbol
from app.services.paper.account_svc import ensure_paper_account
from app.services.paper.order_fill import (
    append_ledger,
    apply_close,
    apply_open,
    calc_fee,
    calc_margin,
    get_position,
    now_utc,
)
from app.services.paper.order_query import order_to_dict
from app.services.paper.specs import ensure_trade_settings, resolve_trade_params


def _dec(value: float | int | str | Decimal) -> Decimal:
    return Decimal(str(value))


def _fee_for_offset(params: dict, offset: str) -> tuple[str, Decimal]:
    """开/平对应手续费参数"""
    fee_mode = str(params["fee_mode"])
    if offset == "close":
        return fee_mode, params["close_fee"]
    return fee_mode, params["open_fee"]


async def place_order(
    session: AsyncSession,
    user_id: UUID,
    symbol: str,
    direction: str,
    offset: str,
    order_type: str,
    price: float | None,
    quantity: int,
    symbol_name: str,
    multiplier: int | None,
    margin_rate: float | None,
    redis_conn: object | None = None,
    trading_mode: str = "live",
    source: str = "manual",
    task_id: UUID | None = None,
) -> dict:
    """下模拟单：按品种默认规格 + 用户设置计保证金/手续费"""
    from app.services.paper.price_band import assert_price_in_band, resolve_last_price
    from app.services.trading_hours import get_session_status

    symbol_clean = symbol.strip().lower()
    try:
        ensure_cny_futures_symbol(symbol_clean)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if not symbol_clean:
        raise HTTPException(status_code=400, detail="合约代码不能为空")
    if direction not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="direction 必须为 buy 或 sell")
    if offset not in ("open", "close"):
        raise HTTPException(status_code=400, detail="offset 必须为 open 或 close")
    if order_type not in ("limit", "market"):
        raise HTTPException(status_code=400, detail="order_type 必须为 limit 或 market")
    if quantity <= 0 or quantity > 10000:
        raise HTTPException(status_code=400, detail="手数须在 1~10000")

    from app.core.trading_mode import MODE_VIRTUAL, normalize_trading_mode

    mode = normalize_trading_mode(trading_mode)
    # 虚拟盘 openctp 7×24：不套用国内交易时段
    if mode == MODE_VIRTUAL:
        market_open = True
        session_status = {
            "is_open": True,
            "message": "虚拟盘 7×24 可交易",
        }
    else:
        session_status = get_session_status(symbol_clean)
        market_open = bool(session_status.get("is_open"))
    if order_type == "market" and not market_open:
        raise HTTPException(
            status_code=400,
            detail=str(
                session_status.get("message")
                or "非交易时段不可市价下单，请改用限价挂单"
            ),
        )

    if price is None or price <= 0:
        raise HTTPException(
            status_code=400,
            detail="请提供有效价格（限价为委托价，市价为最新参考价）",
        )
    order_price = _dec(price)

    # 价格带校验禁用 mock 兜底：无真实参考价时不设带，避免 mock 价误拒真实委托
    last_ref = await resolve_last_price(
        symbol_clean, redis_conn, None, trading_mode, allow_mock=False
    )
    band_info = assert_price_in_band(order_price, last_ref)

    settings = await ensure_trade_settings(session, user_id, trading_mode)
    params = resolve_trade_params(symbol_clean, settings)

    mult = multiplier if multiplier and multiplier > 0 else int(params["multiplier"])
    rate = (
        _dec(margin_rate)
        if margin_rate is not None
        else params["margin_rate"]
    )
    if rate <= 0 or rate > 1:
        raise HTTPException(status_code=400, detail="保证金率须在 (0,1]")

    fee_mode, fee_value = _fee_for_offset(params, offset)

    account = await ensure_paper_account(session, user_id, trading_mode)
    display_name = symbol_name or str(params.get("name") or symbol_clean)
    order = PaperOrder(
        account_id=account.id,
        user_id=user_id,
        symbol=symbol_clean,
        symbol_name=display_name,
        direction=direction,
        offset=offset,
        order_type=order_type,
        price=order_price,
        quantity=quantity,
        filled_qty=0,
        status="pending",
        frozen_margin=Decimal("0"),
        fee=Decimal("0"),
        realized_pnl=Decimal("0"),
        multiplier=mult,
        margin_rate=rate,
        source=source,
        task_id=task_id,
        created_at=now_utc(),
        updated_at=now_utc(),
        filled_at=None,
    )
    session.add(order)
    await session.flush()

    if order_type == "market":
        if offset == "open":
            await apply_open(
                session,
                account,
                order,
                order_price,
                quantity,
                fee_mode,
                fee_value,
                task_id=task_id,
            )
        else:
            await apply_close(
                session,
                account,
                order,
                order_price,
                quantity,
                fee_mode,
                fee_value,
                task_id=task_id,
            )
    else:
        await _place_limit_order(
            session,
            account,
            order,
            order_price,
            quantity,
            mult,
            rate,
            direction,
            offset,
            fee_mode,
            fee_value,
            task_id=task_id,
        )
        if market_open and order.status == "pending" and last_ref is not None:
            await _try_immediate_limit_fill(
                session,
                order,
                last_ref,
                redis_conn,
                symbol_clean,
                trading_mode,
            )

    await session.flush()
    result = order_to_dict(order)
    result["trade_params"] = {
        "code": params["code"],
        "multiplier": mult,
        "margin_rate": float(rate),
        "fee_mode": fee_mode,
        "fee_value": float(fee_value),
        "estimated_margin": float(
            calc_margin(order_price, quantity, mult, rate)
        ),
        "estimated_fee": float(
            calc_fee(order_price, quantity, mult, fee_mode, fee_value)
        ),
        "market_open": market_open,
        "pre_market": order_type == "limit" and not market_open,
        "price_band": band_info,
    }
    if order.status == "filled":
        result["message"] = (
            f"已成交 {order.filled_qty}手 @{float(order.price)}，手续费 ¥{float(order.fee)}"
        )
    elif order.status == "pending" and not market_open:
        result["message"] = "已挂单，开盘后到价将自动成交"
    elif order.status == "pending":
        result["message"] = "已挂单，等待价格触及后成交"
    return result


async def _try_immediate_limit_fill(
    session: AsyncSession,
    order: PaperOrder,
    last_ref: Decimal,
    redis_conn: object | None,
    symbol_clean: str,
    trading_mode: str = "live",
) -> None:
    """限价单下单后立刻尝试成交（读 Redis 盘口）"""
    from app.services.paper.matching.matcher import try_fill_order_now

    bid_d: Decimal | None = None
    ask_d: Decimal | None = None
    try:
        if redis_conn is not None:
            from app.services.market_keys import get_quote_json

            qd = await get_quote_json(redis_conn, trading_mode, symbol_clean)
            if qd:
                if qd.get("bid_price") is not None:
                    bid_d = _dec(qd["bid_price"])
                if qd.get("ask_price") is not None:
                    ask_d = _dec(qd["ask_price"])
    except Exception:
        pass
    await try_fill_order_now(
        session, order, last_ref, bid_d, ask_d, trading_mode
    )


async def _place_limit_order(
    session: AsyncSession,
    account,
    order: PaperOrder,
    order_price: Decimal,
    quantity: int,
    mult: int,
    rate: Decimal,
    direction: str,
    offset: str,
    fee_mode: str,
    fee_value: Decimal,
    task_id: UUID | None = None,
) -> None:
    """限价挂单：开仓冻结保证金，平仓占用可平量"""
    if offset == "open":
        margin = calc_margin(order_price, quantity, mult, rate)
        fee_est = calc_fee(order_price, quantity, mult, fee_mode, fee_value)
        if account.balance < margin + fee_est:
            order.status = "rejected"
            order.updated_at = now_utc()
            raise HTTPException(status_code=400, detail="可用资金不足")
        account.balance = account.balance - margin
        account.frozen_margin = account.frozen_margin + margin
        account.updated_at = now_utc()
        order.frozen_margin = margin
        order.status = "pending"
        append_ledger(
            session,
            account,
            "freeze",
            -margin,
            f"限价挂单冻结 {order.symbol} {quantity}手",
            "order",
            order.id,
        )
        return

    pos_dir = "short" if direction == "buy" else "long"
    pos = await get_position(
        session, account.id, order.symbol, pos_dir, order.source, task_id=task_id
    )
    if pos is None or pos.available_qty < quantity:
        order.status = "rejected"
        order.updated_at = now_utc()
        raise HTTPException(status_code=400, detail="可平手数不足")
    pos.available_qty = pos.available_qty - quantity
    pos.updated_at = now_utc()
    order.status = "pending"
