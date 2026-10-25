import asyncio
from decimal import Decimal, InvalidOperation
import json
import math
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, select

from app.data.contracts import get_code_for_symbol
from app.models.paper.position import PaperPosition
from app.models.paper.account import PaperAccount
from app.services.market_keys import quote_key


MAX_POSITIONS = 1000
MAX_QUOTE_AGE = 120


class AccountValuation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_version: Literal["paper-cache-mark-v1"] = "paper-cache-mark-v1"
    status: Literal["cache_marked", "no_positions", "unavailable"]
    trading_mode: Literal["live", "virtual"]
    currency: str = "CNY"
    book_equity: Decimal
    equity: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    today_pnl: Decimal | None = None
    risk_rate: Decimal | None = None
    calculated_at: float
    earliest_quote_received_at: float | None = None
    issues: list[str] = Field(default_factory=list)
    note: str = "缓存参考估值，不是结算权益或独立行情认证；recv_ts仅为缓存接收时间。今日盈亏缺少交易日基准，不提供估算。"


async def cached_account_valuation(positions, balance: Decimal, frozen: Decimal, redis_conn, trading_mode: str, *, now_ts: float | None = None, currency: str = "CNY") -> AccountValuation:
    if trading_mode not in {"live", "virtual"}:
        raise ValueError("Invalid valuation trading mode")
    now = time.time() if now_ts is None else now_ts
    result = AccountValuation(status="unavailable", trading_mode=trading_mode, currency=currency, book_equity=balance + frozen, calculated_at=now)
    if currency != "CNY" or not balance.is_finite() or not frozen.is_finite():
        result.issues = ["unsupported_currency_or_invalid_book_amount"]
        return result
    if len(positions) > MAX_POSITIONS:
        result.issues = ["position_count_exceeds_valuation_budget"]
        return result
    if not positions:
        result.status, result.equity, result.unrealized_pnl = "no_positions", result.book_equity, Decimal("0")
        result.risk_rate = (frozen / result.equity * 100).quantize(Decimal("0.01")) if result.equity > 0 else None
        return result
    symbols = sorted({position.symbol.strip().lower() for position in positions})
    if redis_conn is None:
        result.issues = ["quote_cache_unavailable"]
        return result
    try:
        async with asyncio.timeout(2):
            raw_quotes = await redis_conn.mget([quote_key(trading_mode, symbol) for symbol in symbols])
        if not isinstance(raw_quotes, (list, tuple)) or len(raw_quotes) != len(symbols):
            raise ValueError("Invalid quote batch")
    except Exception:
        result.issues = ["quote_cache_read_failed"]
        return result
    marks = {}
    received = []
    for symbol, raw in zip(symbols, raw_quotes):
        try:
            if not get_code_for_symbol(symbol) or not isinstance(raw, (str, bytes)) or len(raw) > 65536:
                raise ValueError("Missing quote or unknown instrument")
            quote = json.loads(raw)
            if not isinstance(quote, dict) or str(quote.get("symbol", "")).lower() != symbol:
                raise ValueError("Quote symbol mismatch")
            source = str(quote.get("source") or "").lower()
            virtual_source = source == "openctp" or source.startswith("ctp:openctp")
            live_source = source in {"sina", "simnow", "ctp"} or source.startswith("ctp:")
            if "pulse" in source or (trading_mode == "virtual" and not virtual_source) or (trading_mode == "live" and (virtual_source or not live_source)):
                raise ValueError("Incompatible source")
            stamp, price = quote.get("recv_ts"), quote.get("last_price")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (stamp, price)):
                raise ValueError("Invalid quote fields")
            if price <= 0 or not 0 <= now - stamp <= MAX_QUOTE_AGE:
                raise ValueError("Expired or future quote")
            marks[symbol] = Decimal(str(price))
            received.append(stamp)
        except (ValueError, TypeError, InvalidOperation):
            result.issues.append("missing_invalid_or_stale_quote:" + symbol)
    if result.issues:
        result.issues = result.issues[:10]
        return result
    floating = Decimal("0")
    try:
        for position in positions:
            if position.direction not in {"long", "short"} or position.quantity <= 0 or position.multiplier <= 0 or not position.avg_price.is_finite() or position.avg_price <= 0:
                raise ValueError("Invalid position fields")
            change = marks[position.symbol.strip().lower()] - position.avg_price
            floating += change * position.quantity * position.multiplier * (1 if position.direction == "long" else -1)
        floating = floating.quantize(Decimal("0.01"))
        equity = (result.book_equity + floating).quantize(Decimal("0.01"))
        risk_rate = (frozen / equity * 100).quantize(Decimal("0.01")) if equity > 0 else None
    except (ValueError, InvalidOperation, KeyError):
        result.issues = ["invalid_position_metadata"]
        return result
    result.status = "cache_marked"
    result.unrealized_pnl = floating
    result.equity = equity
    result.risk_rate = risk_rate
    result.earliest_quote_received_at = min(received)
    return result


async def account_valuation(session, user_id: UUID, summary: dict, redis_conn, trading_mode: str) -> AccountValuation:
    query = select(PaperAccount, PaperPosition).outerjoin(PaperPosition, and_(PaperPosition.account_id == PaperAccount.id, PaperPosition.user_id == user_id, PaperPosition.quantity > 0)).where(PaperAccount.id == UUID(summary["account_id"]), PaperAccount.user_id == user_id, PaperAccount.trading_mode == trading_mode).order_by(PaperPosition.id).limit(MAX_POSITIONS + 1)
    rows = (await session.execute(query.execution_options(populate_existing=True))).all()
    if not rows:
        from fastapi import HTTPException

        raise HTTPException(404, "当前登录盘模拟账户不存在")
    account = rows[0][0]
    positions = [row[1] for row in rows if row[1] is not None]
    return await cached_account_valuation(positions, account.balance, account.frozen_margin, redis_conn, trading_mode, currency=account.currency)
