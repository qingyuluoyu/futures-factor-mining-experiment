"""手动迁移：VVTR 次主力（9998）→ 合约表跳到下一流动性合约（备用工具）

常态由 contracts_sync 的「过半自动换月」接管（交割前月 15 日后
自动追加次主力前进），本脚本仅用于需要立即全表提前迁移的场景。
月份只进不退，可重复执行（幂等）。

用法（backend 容器内）：
    python scripts/migrate-contracts-to-next.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redis.asyncio as aioredis  # noqa: E402

from app.core.config import load_settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


async def main() -> None:
    s = load_settings()
    redis_conn = aioredis.from_url(
        f"redis://{s.redis_host}:{s.redis_port}/{s.redis_db}",
        decode_responses=False,
    )
    try:
        from app.services.contracts_sync import migrate_to_next_contracts

        # 无 session_factory：contracts_sync 内部经 asyncpg 直读 VVTR 配置
        targets = await migrate_to_next_contracts(redis_conn, None)
        print(f"迁移完成：{len(targets)} 品种")
        for code in sorted(targets)[:10]:
            print(f"  {code} -> {targets[code]}")
        if len(targets) > 10:
            print(f"  …共 {len(targets)} 品种")
    finally:
        await redis_conn.aclose()


if __name__ == "__main__":
    asyncio.run(main())
