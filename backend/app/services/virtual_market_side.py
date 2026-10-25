"""虚拟盘盘口 / 成交明细 —— 7×24 从 virtual quote 推导

openctp 常无稳定 volume 增量：优先 volume 差，否则 last 变动记 1 手。
不写入 live 键，不污染实盘。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from app.services.market import (
    cache_orderbook,
    cache_trades,
    generate_orderbook,
    generate_trades,
)

logger = logging.getLogger("qihuo.virtual_market_side")

_BJ_TZ = timezone(timedelta(hours=8))

# 进程内上一帧 virtual quote（仅本模块）
_prev_virtual: dict[str, dict[str, Any]] = {}


def generate_trades_virtual(
    quote: dict[str, Any],
    prev_quote: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """virtual 成交：量增优先，否则 last 变动补 1 手（墙钟时间）"""
    trades = generate_trades(quote, prev_quote)
    if trades:
        # 统一显示为北京墙钟，避免 CTP tick_time 与现实错位
        now_s = datetime.now(_BJ_TZ).strftime("%H:%M:%S")
        return [{**t, "time": now_s, "source": t.get("source") or "volume_delta"} for t in trades]

    if prev_quote is None:
        return []
    try:
        last_price = float(quote.get("last_price") or 0)
        prev_last = float(prev_quote.get("last_price") or 0)
    except (TypeError, ValueError):
        return []
    if last_price <= 0 or prev_last <= 0:
        return []
    if last_price == prev_last:
        return []

    if last_price > prev_last:
        direction = "buy"
    else:
        direction = "sell"

    return [
        {
            "time": datetime.now(_BJ_TZ).strftime("%H:%M:%S"),
            "price": last_price,
            "volume": 1,
            "direction": direction,
            "source": "price_tick",
        }
    ]


async def process_virtual_side_markets(
    redis_conn: aioredis.Redis,
    quotes: list[dict[str, Any]],
) -> int:
    """写 virtual orderbook + trades，返回本轮成交条数"""
    global _prev_virtual
    if not quotes:
        return 0

    trade_n = 0
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().lower()
        if not symbol:
            continue
        # ctp / 脉冲续价可推成交；新浪补价不造虚假 tick 流
        src = str(quote.get("source") or "")
        if src.startswith("sina"):
            continue
        if not (
            src.startswith("ctp")
            or "openctp" in src
            or "pulse" in src
            or src == ""
        ):
            continue

        q = dict(quote)
        q["symbol"] = symbol
        try:
            orderbook = generate_orderbook(q)
            await cache_orderbook(redis_conn, orderbook, "virtual")
            # 大单检测：显著放量即推送原始事件
            from app.services.notify.big_order_detect import detect_and_publish

            await detect_and_publish(redis_conn, "virtual", orderbook)
        except Exception:
            logger.exception("virtual 盘口写入失败 %s", symbol)

        prev = _prev_virtual.get(symbol)
        try:
            trades = generate_trades_virtual(q, prev)
            if trades:
                await cache_trades(redis_conn, symbol, trades, "virtual")
                trade_n += len(trades)
        except Exception:
            logger.exception("virtual 成交写入失败 %s", symbol)

        _prev_virtual[symbol] = q

    return trade_n
