"""用新浪全量覆盖 Redis market:quote:*（矫正 CTP/SimNow 污染的 OHLC）

用法: docker exec -w /app qihuo-backend python /tmp/correct-all-quotes-sina.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path and os.path.isdir(_BACKEND):
    sys.path.insert(0, _BACKEND)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("correct_all_quotes")


async def main() -> None:
    from app.data.contracts import get_code_for_symbol, get_decimal_places
    from app.data.quote_schema import filter_valid_quotes
    from app.data.sina_quote import fetch_sina_quotes

    import redis.asyncio as aioredis

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    redis_conn = aioredis.Redis(host=host, port=port, db=db, decode_responses=True)

    raw = await asyncio.to_thread(fetch_sina_quotes)
    quotes = filter_valid_quotes(raw)
    n = 0
    for q in quotes:
        sym = str(q.get("symbol") or "").lower()
        if not sym or float(q.get("last_price") or 0) <= 0:
            continue
        q["symbol"] = sym
        q["source"] = "sina"
        q["decimal_places"] = get_decimal_places(get_code_for_symbol(sym))
        await redis_conn.set(
            f"market:quote:{sym}",
            json.dumps(q, ensure_ascii=False),
            ex=7 * 24 * 3600,
        )
        n += 1
    # 清掉被污染的 forming K
    deleted = 0
    async for key in redis_conn.scan_iter(match="kline:realtime:*", count=200):
        await redis_conn.delete(key)
        deleted += 1
    await redis_conn.aclose()
    logger.info("写入新浪 quote %d 条，删除 realtime kline %d", n, deleted)
    print(json.dumps({"quotes": n, "deleted_realtime": deleted}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
