"""虚拟盘补行情 —— openctp 无 tick 时用新浪价填充 virtual 命名空间

仅写入「当前不存在」的 market:virtual:quote:*，不覆盖 openctp 真实行情。
source 标记 sina_virtual_fill，便于排查。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.data.contracts import ALL_CONTRACTS
from app.services.market_keys import quote_key

logger = logging.getLogger("qihuo.virtual_quote_fill")

_TTL = 7 * 24 * 3600


async def fill_missing_virtual_quotes_from_sina(
    redis_conn: aioredis.Redis,
) -> int:
    """对 ALL_CONTRACTS 中 virtual 缺价的合约，用新浪源键补写。

    sina-bridge 容器写 source 键，本进程只读 Redis 不出网。
    返回新写入条数。
    """
    from app.services.market_channel import collect_source_quotes_from_redis

    missing: list[str] = []
    for c in ALL_CONTRACTS:
        sym = str(c.get("symbol") or "").strip().lower()
        if not sym:
            continue
        try:
            exists = await redis_conn.exists(quote_key("virtual", sym))
        except Exception:
            continue
        if not exists:
            missing.append(sym)
    if not missing:
        return 0

    quotes = await collect_source_quotes_from_redis(redis_conn, "sina")

    by_sym = {
        str(q.get("symbol") or "").strip().lower(): q
        for q in quotes
        if q.get("symbol")
    }
    n = 0
    for sym in missing:
        q = by_sym.get(sym)
        if not q or float(q.get("last_price") or 0) <= 0:
            continue
        payload = dict(q)
        payload["source"] = "sina_virtual_fill"
        payload["symbol"] = sym
        try:
            await redis_conn.set(
                quote_key("virtual", sym),
                json.dumps(payload, ensure_ascii=False),
                ex=_TTL,
            )
            n += 1
        except Exception:
            logger.exception("写入 virtual 补行情失败 %s", sym)
    if n:
        logger.info("virtual 补行情写入 %d 条（openctp 缺价）", n)
    return n
