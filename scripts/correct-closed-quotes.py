"""用新浪最新价矫正休市品种 Redis quote（夜盘交易中品种不动）

用法（在 backend 容器或 backend 目录）:
  python /path/to/correct-closed-quotes.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

# 允许从 backend 根目录 / 容器 /app 运行
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND_ROOT not in sys.path and os.path.isdir(_BACKEND_ROOT):
    sys.path.insert(0, _BACKEND_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("correct_closed_quotes")


async def correct_closed_quotes() -> dict[str, Any]:
    from app.data.contracts import get_code_for_symbol, get_decimal_places
    from app.data.quote_schema import filter_valid_quotes
    from app.data.sina_quote import fetch_sina_quotes
    from app.services.trading_hours import beijing_now, is_symbol_trading

    import redis.asyncio as aioredis

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    redis_conn = aioredis.Redis(host=host, port=port, db=db, decode_responses=True)

    now = beijing_now()
    logger.info("开始矫正休市报价 now_bj=%s redis=%s:%s/%s", now, host, port, db)

    raw = await asyncio.to_thread(fetch_sina_quotes)
    quotes = filter_valid_quotes(raw)
    logger.info("新浪有效行情 %d 条", len(quotes))

    fixed = 0
    skipped_open = 0
    skipped_bad = 0
    samples: list[dict[str, Any]] = []

    for q in quotes:
        symbol = str(q.get("symbol") or "").strip().lower()
        if not symbol:
            continue
        if is_symbol_trading(symbol):
            skipped_open += 1
            continue
        try:
            last = float(q.get("last_price") or 0)
        except (TypeError, ValueError):
            skipped_bad += 1
            continue
        if last <= 0:
            skipped_bad += 1
            continue

        code = get_code_for_symbol(symbol)
        q["decimal_places"] = get_decimal_places(code)
        q["source"] = "sina_correct"
        q["symbol"] = symbol

        key = f"market:quote:{symbol}"
        old_raw = await redis_conn.get(key)
        old_last: float | None = None
        if old_raw:
            try:
                old_last = float(json.loads(old_raw).get("last_price") or 0) or None
            except (json.JSONDecodeError, TypeError, ValueError):
                old_last = None

        await redis_conn.set(key, json.dumps(q, ensure_ascii=False), ex=7 * 24 * 3600)
        fixed += 1
        if len(samples) < 20:
            samples.append(
                {
                    "symbol": symbol,
                    "old_last": old_last,
                    "new_last": last,
                    "tick_time": q.get("tick_time"),
                }
            )

    await redis_conn.aclose()
    result = {
        "now_bj": now.strftime("%Y-%m-%d %H:%M:%S%z"),
        "sina_count": len(quotes),
        "fixed": fixed,
        "skipped_open": skipped_open,
        "skipped_bad": skipped_bad,
        "samples": samples,
    }
    logger.info(
        "完成 fixed=%s skipped_open=%s skipped_bad=%s",
        fixed,
        skipped_open,
        skipped_bad,
    )
    return result


def main() -> None:
    result = asyncio.run(correct_closed_quotes())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
