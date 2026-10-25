"""tickdata 缺口补数 —— 用新浪财经接口拉取并入库（幂等，可重复跑）

背景：pg-tickdata 由外部 CSV 批次灌入，上游断供时库会滞后数周
（2026-06 曾停更近 2 个月）。本脚本用系统现成的新浪 K 线抓取层
（app/data/sina_kline.py）把缺口补进 kline 表：
- 只补「库内最新 bar 之后」的数据 + ON CONFLICT DO NOTHING，纯增量
- 限速请求（新浪有封禁风险），单品种单周期失败不影响其余
- 交易时段感知聚合：5/15/30/60m 用 1m 聚合保证 OHLCV 准确

新浪各周期能拿到的深度：1d 全历史；60m≈10 个月；30m≈5 个月；
15m≈2.5 个月；5m≈1-2 个月；1m≈4 天。补 2 个月级别的缺口足够。

用法（在 qihuo-backend 容器内，/app 工作目录）：
  python /tmp/backfill-tickdata-from-sina.py                     # 全品种全周期
  python /tmp/backfill-tickdata-from-sina.py --products rb,cu    # 指定品种
  python /tmp/backfill-tickdata-from-sina.py --dry-run           # 只看会补多少
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta

import asyncpg

sys.path.insert(0, "/app")

from app.data.contracts import ALL_CONTRACTS, get_code_for_symbol  # noqa: E402
from app.data.sina_kline import (  # noqa: E402
    fetch_daily_kline,
    fetch_minute_kline,
)

PERIOD_MAP = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1d": 1440}


def parse_ts(time_str: str) -> datetime | None:
    """解析 bar 时间：分钟 'YYYY-MM-DD HH:MM:SS' / 日线 'YYYY-MM-DD'"""
    t = str(time_str or "").strip()
    if not t:
        return None
    try:
        if len(t) >= 19:
            return datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
        if len(t) >= 16:
            return datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
        if len(t) >= 10:
            return datetime.strptime(t[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return None

# 中文所名 → kline.exchange 代码（库内已有该合约时以库内值为准）
_EXCHANGE_MAP = {
    "上期所": "SHFE", "大商所": "DCE", "郑商所": "CZCE",
    "中金所": "CFFEX", "能源中心": "INE", "上期能源": "INE", "广期所": "GFEX",
}


def dominant_contracts() -> list[dict]:
    """ALL_CONTRACTS 每品种的第一个 symbol = 当前主力合约"""
    seen: set[str] = set()
    out: list[dict] = []
    for c in ALL_CONTRACTS:
        code = str(c.get("code") or "").upper()
        sym = str(c.get("symbol") or "")
        if code and sym and code not in seen:
            seen.add(code)
            out.append({"symbol": sym, "code": code,
                        "exchange_cn": str(c.get("exchange") or "")})
    return out


async def fetch_bars(symbol: str, period: str) -> list[dict]:
    if period == "1d":
        return fetch_daily_kline(symbol)
    return fetch_minute_kline(symbol, period)


async def db_latest(conn, period: int, instrument: str):
    """库内该合约该周期最新 ts（ts 下限触发月分区裁剪）"""
    lo = datetime.now() - timedelta(days=120)
    r = await conn.fetchrow(
        "SELECT max(ts) mx FROM kline WHERE period=$1 AND instrument=$2 AND ts>=$3",
        period, instrument, lo,
    )
    return r["mx"] if r else None


async def db_exchange(conn, instrument: str) -> str | None:
    lo = datetime.now() - timedelta(days=400)
    r = await conn.fetchrow(
        "SELECT exchange FROM kline WHERE period=1440 AND instrument=$1 AND ts>=$2 LIMIT 1",
        instrument, lo,
    )
    return r["exchange"] if r else None


async def insert_new(conn, period: int, exchange: str, instrument: str,
                     bars: list[dict], after_ts) -> int:
    rows = []
    seen: set = set()
    for b in bars:
        dt = parse_ts(str(b.get("time") or ""))
        if dt is None:
            continue
        if after_ts is not None and dt <= after_ts:
            continue
        if dt in seen:
            continue
        seen.add(dt)
        oi = b.get("open_interest")
        rows.append((
            period, exchange, instrument, dt,
            float(b.get("open") or 0), float(b.get("high") or 0),
            float(b.get("low") or 0), float(b.get("close") or 0),
            int(b.get("volume") or 0), None,
            int(oi) if oi is not None else None,
        ))
    if not rows:
        return 0
    await conn.executemany(
        "INSERT INTO kline (period, exchange, instrument, ts, open, high, low, "
        "close, volume, amount, position) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
        "ON CONFLICT (period, instrument, ts) DO NOTHING",
        rows,
    )
    return len(rows)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--products", default="", help="品种代码逗号分隔（如 rb,cu），空=全部")
    ap.add_argument("--periods", default="1d,60m,30m,15m,5m,1m")
    ap.add_argument("--sleep", type=float, default=1.5, help="请求间隔秒（新浪限速）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    periods = [p for p in args.periods.split(",") if p in PERIOD_MAP]
    contracts = dominant_contracts()
    if args.products:
        want = {x.strip().lower() for x in args.products.split(",")}
        contracts = [c for c in contracts if c["code"].lower() in want]

    pool = await asyncpg.create_pool(
        host=os.environ["TICKDATA_POSTGRES_HOST"],
        port=int(os.environ.get("TICKDATA_POSTGRES_PORT", 5434)),
        database=os.environ.get("TICKDATA_POSTGRES_DB", "tickdata"),
        user=os.environ["TICKDATA_POSTGRES_USER"],
        password=os.environ["TICKDATA_POSTGRES_PASSWORD"],
        min_size=1, max_size=2, command_timeout=15,
    )
    total_new = 0
    t0 = time.time()
    try:
        async with pool.acquire() as conn:
            for c in contracts:
                sym = c["symbol"]
                ins = sym.strip().lower()
                if get_code_for_symbol(ins) != c["code"]:
                    continue  # 符号异常，跳过
                ex_db = await db_exchange(conn, ins)
                exchange = ex_db or _EXCHANGE_MAP.get(c["exchange_cn"], "")
                for p in periods:
                    pg_period = PERIOD_MAP[p]
                    try:
                        latest = await db_latest(conn, pg_period, ins)
                        bars = await fetch_bars(sym, p)
                        after = latest
                        if args.dry_run:
                            cand = [b for b in bars if parse_ts(str(b.get("time") or ""))]
                            newer = [b for b in cand if after is None
                                     or parse_ts(str(b["time"])) > after]
                            print(f"[dry] {ins} {p}: sina {len(bars)} 根, "
                                  f"库内最新 {after}, 待补 {len(newer)}")
                        else:
                            n = await insert_new(conn, pg_period, exchange, ins, bars, latest)
                            total_new += n
                            print(f"{ins} {p}: +{n} rows (库内原最新 {latest})", flush=True)
                    except Exception as e:
                        print(f"{ins} {p}: FAIL {type(e).__name__}: {e}", flush=True)
                    await asyncio.sleep(args.sleep)
    finally:
        await pool.close()
    print(f"done: {len(contracts)} contracts, total +{total_new} rows, "
          f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
