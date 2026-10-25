"""限价挂单撮合 —— 开盘后按最新价 / 盘口触发成交

规则：
  买限价：最新价 ≤ 委托价 或 卖一 ≤ 委托价 → 成交
  卖限价：最新价 ≥ 委托价 或 买一 ≥ 委托价 → 成交
  成交价使用委托限价
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.paper.account import PaperAccount
from app.models.paper.order import PaperOrder
from app.services.paper.account_svc import ensure_paper_account
from app.services.paper.matching.pending_fill import (
    fill_pending_close,
    fill_pending_open,
)
from app.services.paper.specs import ensure_trade_settings, resolve_trade_params
from app.services.trading_hours import is_symbol_trading

logger = logging.getLogger("qihuo.paper.matcher")


def should_fill(
    direction: str,
    limit_price: Decimal,
    last_price: Decimal,
    bid: Decimal | None,
    ask: Decimal | None,
) -> bool:
    """是否触发限价成交（可立即成交的限价单）"""
    if direction == "buy":
        if last_price > 0 and last_price <= limit_price:
            return True
        if ask is not None and ask > 0 and ask <= limit_price:
            return True
        return False
    if last_price > 0 and last_price >= limit_price:
        return True
    if bid is not None and bid > 0 and bid >= limit_price:
        return True
    return False


def _to_dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except Exception:
        return None
    if d <= 0:
        return None
    return d


async def _fill_one(
    session: AsyncSession,
    order: PaperOrder,
    last_price: Decimal,
    bid: Decimal | None,
    ask: Decimal | None,
) -> bool:
    """尝试成交单笔 pending 限价单"""
    if order.status != "pending" or order.order_type != "limit":
        return False
    if not should_fill(order.direction, order.price, last_price, bid, ask):
        return False
    acc_stmt = select(PaperAccount).where(PaperAccount.id == order.account_id)
    account = (await session.execute(acc_stmt)).scalar_one_or_none()
    if account is None:
        return False
    mode = str(getattr(account, "trading_mode", None) or "live")
    if account.user_id != order.user_id:
        return False
    account = await ensure_paper_account(session, order.user_id, mode)
    order = (await session.execute(
        select(PaperOrder).where(
            PaperOrder.id == order.id,
            PaperOrder.account_id == account.id,
            PaperOrder.user_id == account.user_id,
        ).with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if order is None or order.status != "pending" or order.order_type != "limit":
        return False
    if not should_fill(order.direction, order.price, last_price, bid, ask):
        return False
    settings = await ensure_trade_settings(session, order.user_id, mode)
    params = resolve_trade_params(order.symbol, settings)
    fee_mode = str(params["fee_mode"])
    fee_value = (
        params["close_fee"] if order.offset == "close" else params["open_fee"]
    )
    fill_price = order.price
    try:
        if order.offset == "open":
            await fill_pending_open(
                session, account, order, fill_price, fee_mode, fee_value
            )
        else:
            await fill_pending_close(
                session, account, order, fill_price, fee_mode, fee_value
            )
        if order.status == "filled":
            logger.info(
                "挂单成交 %s %s %s %s手 @%s last=%s",
                order.id,
                order.symbol,
                order.direction,
                order.filled_qty,
                fill_price,
                last_price,
            )
            return True
    except Exception:
        logger.exception("撮合失败 order=%s", order.id)
    return False


async def match_symbol(
    session: AsyncSession,
    symbol: str,
    last_price: Decimal,
    bid: Decimal | None,
    ask: Decimal | None,
) -> int:
    """撮合某合约全部 pending 限价单，返回成交笔数（兼容旧调用）"""
    return await match_symbol_for_mode(
        session, symbol, last_price, bid, ask, "live"
    )


async def match_symbol_for_mode(
    session: AsyncSession,
    symbol: str,
    last_price: Decimal,
    bid: Decimal | None,
    ask: Decimal | None,
    trading_mode: str,
) -> int:
    """撮合某合约、指定盘账户下的 pending 限价单"""
    return await _match_book(session, {(trading_mode, symbol): (last_price, bid, ask)})


async def _match_book(
    session: AsyncSession,
    book: dict[tuple[str, str], tuple[Decimal, Decimal | None, Decimal | None]],
) -> int:
    eligible = {
        pair: prices
        for pair, prices in book.items()
        if prices[0] > 0 and (pair[0] == "virtual" or is_symbol_trading(pair[1]))
    }
    if not eligible:
        return 0

    stmt = (
        select(PaperOrder)
        .join(PaperAccount, PaperAccount.id == PaperOrder.account_id)
        .where(
            PaperOrder.status == "pending",
            PaperOrder.order_type == "limit",
            tuple_(PaperAccount.trading_mode, PaperOrder.symbol).in_(list(eligible)),
        )
        .order_by(PaperOrder.created_at.asc())
    )
    orders = list((await session.execute(stmt)).scalars().all())
    if not orders:
        return 0
    accounts = list((await session.execute(
        select(PaperAccount).where(PaperAccount.id.in_({order.account_id for order in orders}))
    )).scalars().all())
    modes = {account.id: account.trading_mode for account in accounts}
    rank = {pair: index for index, pair in enumerate(eligible)}
    orders.sort(key=lambda order: (
        str(order.user_id),
        modes[order.account_id],
        rank[(modes[order.account_id], order.symbol)],
        order.created_at,
        str(order.id),
    ))
    filled = 0
    for order in orders:
        last_price, bid, ask = eligible[(modes[order.account_id], order.symbol)]
        if await _fill_one(session, order, last_price, bid, ask):
            filled += 1
    return filled


async def try_fill_order_now(
    session: AsyncSession,
    order: PaperOrder,
    last_price: Decimal,
    bid: Decimal | None,
    ask: Decimal | None,
    trading_mode: str = "live",
) -> bool:
    """下单后立即尝试成交（live 需开盘；virtual 7×24）"""
    if last_price <= 0:
        return False
    if trading_mode != "virtual" and not is_symbol_trading(order.symbol):
        return False
    return await _fill_one(session, order, last_price, bid, ask)


def _book_from_quotes(
    quotes: list[dict],
) -> dict[str, tuple[Decimal, Decimal | None, Decimal | None]]:
    book: dict[str, tuple[Decimal, Decimal | None, Decimal | None]] = {}
    for q in quotes:
        sym = str(q.get("symbol") or "").strip().lower()
        last = _to_dec(q.get("last_price"))
        if not sym or last is None:
            continue
        bid = _to_dec(q.get("bid_price"))
        ask = _to_dec(q.get("ask_price"))
        book[sym] = (last, bid, ask)
    return book


async def match_quotes(
    session_factory: async_sessionmaker[AsyncSession] | None,
    quotes: list[dict],
) -> int:
    """对一批行情触发撮合"""
    if session_factory is None or not quotes:
        return 0
    book = _book_from_quotes(quotes)
    if not book:
        return 0

    total = 0
    async with session_factory() as session:
        try:
            total = await _match_book(session, {("live", symbol): prices for symbol, prices in book.items()})
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("批量撮合事务失败")
            return 0
    return total


async def match_pending_from_redis(
    session_factory: async_sessionmaker[AsyncSession] | None,
    redis_conn: object | None,
) -> int:
    """扫描全部 pending 合约，从 Redis 取行情撮合"""
    if session_factory is None or redis_conn is None:
        return 0

    async with session_factory() as session:
        try:
            from app.services.market_keys import get_quote_json

            # 按账户盘模式分别取价：live 与 virtual 互不串
            orders_stmt = select(PaperOrder).where(
                PaperOrder.status == "pending",
                PaperOrder.order_type == "limit",
            )
            pending_orders = list(
                (await session.execute(orders_stmt)).scalars().all()
            )
            if not pending_orders:
                return 0
            acc_ids = {o.account_id for o in pending_orders}
            acc_rows = list(
                (
                    await session.execute(
                        select(PaperAccount).where(PaperAccount.id.in_(acc_ids))
                    )
                ).scalars().all()
            )
            acc_mode = {
                a.id: str(getattr(a, "trading_mode", None) or "live")
                for a in acc_rows
            }
            pairs: set[tuple[str, str]] = set()
            for o in pending_orders:
                mode = acc_mode.get(o.account_id, "live")
                pairs.add((mode, str(o.symbol).strip().lower()))

            book = {}
            for mode, symbol in sorted(pairs):
                qd = await get_quote_json(redis_conn, mode, symbol)
                if not qd:
                    continue
                last = _to_dec(qd.get("last_price"))
                if last is None:
                    continue
                bid = _to_dec(qd.get("bid_price"))
                ask = _to_dec(qd.get("ask_price"))
                book[(mode, symbol)] = (last, bid, ask)
            total = await _match_book(session, book)
            await session.commit()
            return total
        except Exception:
            await session.rollback()
            logger.exception("Redis 挂单扫描撮合失败")
            return 0
