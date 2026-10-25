"""清空并重拉 K 线 Redis 缓存（仅新浪历史，不含 7×24 污染 forming）

用法（backend 容器）:
  python /tmp/repair-kline-cache.py
  python /tmp/repair-kline-cache.py rb2610
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND_ROOT not in sys.path and os.path.isdir(_BACKEND_ROOT):
    sys.path.insert(0, _BACKEND_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("repair_kline")

PERIODS = ["1m", "5m", "15m", "30m", "60m", "1d"]


async def repair(symbols: list[str] | None) -> dict[str, Any]:
    from app.data.contracts import ALL_CONTRACTS
    from app.data.sina_kline import fetch_daily_kline, fetch_minute_kline

    import redis.asyncio as aioredis

    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    redis_conn = aioredis.Redis(host=host, port=port, db=db, decode_responses=True)

    # 1) 清空全部 kline 缓存（history + realtime）
    deleted = 0
    async for key in redis_conn.scan_iter(match="kline:*", count=500):
        await redis_conn.delete(key)
        deleted += 1
    logger.info("已删除 kline:* 键 %d 个", deleted)

    target = symbols
    if not target:
        target = [str(c["symbol"]).lower() for c in ALL_CONTRACTS]

    rebuilt = 0
    failed: list[str] = []
    samples: dict[str, Any] = {}

    for symbol in target:
        for period in PERIODS:
            try:
                if period == "1d":
                    bars = await asyncio.to_thread(fetch_daily_kline, symbol)
                else:
                    bars = await asyncio.to_thread(fetch_minute_kline, symbol, period)
            except Exception as exc:
                failed.append(f"{symbol}:{period}:{exc}")
                continue
            if not bars:
                failed.append(f"{symbol}:{period}:empty")
                continue
            key = f"kline:history:{symbol}:{period}"
            # 非交易时段给更长 TTL，避免马上又被脏数据覆盖前过期
            await redis_conn.set(key, json.dumps(bars, ensure_ascii=False), ex=3600)
            rebuilt += 1
            if symbol in ("rb2610", "pp2609", "au2612") and period in ("15m", "1d"):
                last = bars[-1]
                samples[f"{symbol}:{period}"] = {
                    "n": len(bars),
                    "last": {
                        "time": last.get("time"),
                        "open": last.get("open"),
                        "high": last.get("high"),
                        "low": last.get("low"),
                        "close": last.get("close"),
                    },
                }
        # 轻微限速，避免新浪限流
        await asyncio.sleep(0.05)

    await redis_conn.aclose()
    return {
        "deleted_keys": deleted,
        "rebuilt": rebuilt,
        "failed_count": len(failed),
        "failed_head": failed[:20],
        "samples": samples,
    }


def main() -> None:
    args = [a.strip().lower() for a in sys.argv[1:] if a.strip()]
    result = asyncio.run(repair(args or None))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
