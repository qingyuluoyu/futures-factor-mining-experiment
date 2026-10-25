"""一次性回填：VVTR K线（近 N 天，默认 7）→ pg-tickdata

用法（backend 容器内）：
    python scripts/backfill-tickdata-vvtr.py            # 默认 7 天
    python scripts/backfill-tickdata-vvtr.py --days 30  # 自定义天数

主力 × 6 周期（1m/5m/15m/30m/60m/1d）串行拉取，全局 1 rps 节流，
幂等 upsert 可重复执行。1m 受官方限制仅近 6 个月（回填 ≤180 天）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.asyncio as aioredis  # noqa: E402

from app.core.config import load_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=7.0)
    args = parser.parse_args()

    s = load_settings()
    redis_conn = aioredis.from_url(
        f"redis://{s.redis_host}:{s.redis_port}/{s.redis_db}",
        decode_responses=False,
    )
    try:
        from app.services.vvtr_tickdata_sync import sync_masters_to_tickdata

        stats = await sync_masters_to_tickdata(
            redis_conn, days=args.days, session_factory=None
        )
        total = sum(stats.values())
        print(f"回填完成（{args.days} 天）：共写入 {total} 行")
        for period, n in stats.items():
            if n:
                print(f"  {period}: {n} 行")
    finally:
        await redis_conn.aclose()


if __name__ == "__main__":
    asyncio.run(main())
