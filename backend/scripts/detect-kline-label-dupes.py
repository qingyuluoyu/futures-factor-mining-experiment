"""kline_minute 双时间标签诊断（只读报告，不改数据）

背景：K 线时间键已统一为「周期起点」语义。历史上新浪/tqsdk 路径曾用
「周期结束」标签写库，同一分钟可能留下 ts 与 ts+60s 两行（uq_code_period_ts
拦不住）。本脚本按品种统计近 N 天内「同一 symbol 在相邻分钟成对出现」的
行数，用于评估遗留规模；修复以 kline_sync 的 VVTR 权威 upsert 覆盖起点
行为主，一般无需手工删行。

用法（服务器上、backend 容器或同网络环境）：
    python -m scripts.detect-kline-label-dupes [--days 30] [--min-pairs 50]
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

# 与 app.core.config 相同的环境变量名，避免拉起整个应用
_ENV_MAP = {
    "host": "POSTGRES_HOST",
    "port": "POSTGRES_PORT",
    "user": "POSTGRES_USER",
    "password": "POSTGRES_PASSWORD",
    "database": "POSTGRES_DB",
}


def _dsn() -> dict[str, Any]:
    return {k: os.getenv(v, "") for k, v in _ENV_MAP.items()}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--min-pairs", type=int, default=50)
    args = parser.parse_args()

    import asyncpg

    dsn = _dsn()
    if not all(dsn.values()):
        print("缺少 POSTGRES_* 环境变量（或在 backend 容器内运行）")
        return 2

    conn = await asyncpg.connect(
        host=dsn["host"], port=int(dsn["port"]), user=dsn["user"],
        password=dsn["password"], database=dsn["database"],
    )
    try:
        rows = await conn.fetch(
            """
            SELECT a.code, a.symbol, a.period, count(*) AS dup_pairs
            FROM kline_minute a
            JOIN kline_minute b
              ON b.code = a.code
             AND b.symbol = a.symbol
             AND b.period = a.period
             AND b.ts = a.ts + interval '1 minute'
            WHERE a.ts >= now() - ($1 || ' days')::interval
            GROUP BY a.code, a.symbol, a.period
            HAVING count(*) >= $2
            ORDER BY dup_pairs DESC
            """,
            str(args.days), args.min_pairs,
        )
    finally:
        await conn.close()

    if not rows:
        print(f"近 {args.days} 天未发现成对双标签行（阈值 {args.min_pairs} 对）")
        return 0
    total = 0
    print(f"{'code':<8}{'symbol':<10}{'period':<8}{'dup_pairs':>10}")
    for r in rows:
        total += int(r["dup_pairs"])
        print(
            f"{r['code']:<8}{r['symbol']:<10}{r['period']:<8}"
            f"{int(r['dup_pairs']):>10}"
        )
    print(f"合计 {total} 对（起点/结束双标签并存，多周期聚合时会错桶一个周期）")
    print("修复建议：以 kline_sync 权威 upsert 覆盖为主；确认后可按报告人工清理")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
