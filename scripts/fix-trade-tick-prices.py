"""一次性数据修复：把 trade_tick.price 按品种最小变动价位取整（去掉多余小数）

背景：早期写入的 trade_tick 用了均价(average_price)，带大量小数。
此脚本把存量记录的 price 改为按品种 tick 取整（螺纹钢→整数，黄金→2 位），
与新写入逻辑（trade_tick_svc._round_price）一致。

幂等：重复执行结果不变（已取整的再取整仍是原值）。

用法（在 33 backend 容器内，或本地 backend 目录）:
  uv run python scripts/fix-trade-tick-prices.py
  uv run python scripts/fix-trade-tick-prices.py --dry-run   # 只统计不改
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

# 把 backend 加入 import 路径
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND_ROOT not in sys.path and os.path.isdir(_BACKEND_ROOT):
    sys.path.insert(0, _BACKEND_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fix_trade_tick_prices")

BATCH_SIZE = 1000


def _decimal_places_from_tick(tick: float) -> int:
    if tick >= 1:
        return 0
    s = f"{tick:.10f}".rstrip("0").split(".")
    return len(s[1]) if len(s) > 1 else 0


async def fix(dry_run: bool) -> dict[str, Any]:
    from sqlalchemy import select, text

    from app.core.config import load_settings
    from app.core.database import create_database_engine
    from app.data.contracts import get_tick_size, round_by_tick
    from app.data.product_specs import extract_product_code
    from app.models.big_order import TradeTick

    settings = load_settings()
    engine = create_database_engine(settings)

    total_scanned = 0
    total_updated = 0
    # 缓存 symbol → (tick, decimals) 避免重复计算
    symbol_cfg: dict[str, tuple[float, int]] = {}

    try:
        async with engine.begin() as conn:
            # 分批扫描（keyset pagination 按 occurred_at 顺序）
            offset = 0
            while True:
                rows = await conn.execute(
                    text(
                        "SELECT id, symbol, price FROM trade_tick "
                        "ORDER BY occurred_at LIMIT :lim OFFSET :off"
                    ),
                    {"lim": BATCH_SIZE, "off": offset},
                )
                batch = rows.fetchall()
                if not batch:
                    break

                updates: list[tuple[float, str]] = []  # (new_price, id_str)
                for row in batch:
                    total_scanned += 1
                    rid = str(row[0])
                    symbol = str(row[1]).lower()
                    old_price = float(row[2])

                    cfg = symbol_cfg.get(symbol)
                    if cfg is None:
                        try:
                            code = extract_product_code(symbol)
                            tick = get_tick_size(code)
                            decimals = _decimal_places_from_tick(tick)
                        except Exception:
                            tick, decimals = 1.0, 0
                        symbol_cfg[symbol] = (tick, decimals)
                        cfg = (tick, decimals)

                    tick, decimals = cfg
                    try:
                        code = extract_product_code(symbol)
                        new_price = round(round_by_tick(old_price, code), decimals)
                    except Exception:
                        new_price = round(old_price, decimals)

                    # 仅当价格变化时更新（浮点比较用 round 后相等判定）
                    if round(new_price, decimals) != round(old_price, decimals):
                        updates.append((new_price, rid))

                # 批量更新（按 id）
                if updates and not dry_run:
                    for new_price, rid in updates:
                        await conn.execute(
                            text("UPDATE trade_tick SET price = :p WHERE id = :id"),
                            {"p": new_price, "id": rid},
                        )
                    total_updated += len(updates)
                elif updates and dry_run:
                    total_updated += len(updates)

                offset += BATCH_SIZE
                if len(batch) < BATCH_SIZE:
                    break

        return {
            "scanned": total_scanned,
            "would_update" if dry_run else "updated": total_updated,
            "symbols": len(symbol_cfg),
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="修正 trade_tick.price 按品种 tick 取整")
    parser.add_argument("--dry-run", action="store_true", help="只统计不修改")
    args = parser.parse_args()

    result = asyncio.run(fix(args.dry_run))
    action = "将更新" if args.dry_run else "已更新"
    logger.info(
        "完成：扫描 %d 行，%s %d 行，涉及 %d 个品种",
        result["scanned"],
        action,
        result.get("updated", result.get("would_update", 0)),
        result["symbols"],
    )


if __name__ == "__main__":
    main()
