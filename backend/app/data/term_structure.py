"""期限结构特征源 —— 近/远月价差的日频构建（tickdata，只读）

近月=当日成交量最大的具体合约，远月=次大，远远月=第三。
TERM_SPREAD = log(近/远)，TERM_SLOPE = log(远/远远)。
升水结构（近强远弱）为正、贴水为负——期限结构斜率对中期方向有
经典预测力（carry/roll-yield），是期货特有而现有特征库缺失的一族。

设计：异步预计算，按交易日映射到 bar（features 层保持纯同步无 IO，
缺数据的品种特征置零——与持仓量缺失的处理同款，不造假信号）。
"""

from __future__ import annotations

import logging
import math
import time as _time
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger("qihuo.term_structure")

_TTL = 24 * 3600
# product -> (loaded_at, {date_iso: (spread, slope)})
_cache: dict[str, tuple[float, dict[str, tuple[float, float]]]] = {}


async def fetch_term_spread_daily(
    product: str, d_from: str, d_to: str
) -> dict[str, tuple[float, float]]:
    """日频期限结构：{date_iso: (TERM_SPREAD, TERM_SLOPE)}，带 24h 缓存

    近/远按当日成交量排序取具体合约（排除 9999/8888 连续序列，用
    ^(product)\\d{4}$ 正则防前缀误匹配，如 i 误配 if/im/ic）。
    失败/无数据返回空 dict（调用方特征置零）。
    """
    product = (product or "").strip().lower()
    if not product:
        return {}
    key = product
    now = _time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1]

    from app.data.tickdata_kline import _get_pool

    pool = await _get_pool()
    if pool is None:
        return {}
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ts, instrument, volume, close FROM kline "
                "WHERE period = 1440 AND instrument ~ $1 "
                "AND ts >= $2 AND ts < $3 "
                "ORDER BY ts",
                f"^{product}\\d{{4}}$",
                date.fromisoformat(d_from),
                date.fromisoformat(d_to) + timedelta(days=1),
                timeout=60,
            )
    except Exception:
        logger.debug("期限结构查询失败 %s（特征置零）", product, exc_info=True)
        return {}

    by_day: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        d = r["ts"].date().isoformat()
        c = float(r["close"] or 0)
        if c <= 0:
            continue
        by_day.setdefault(d, []).append((int(r["volume"] or 0), c))
    out: dict[str, tuple[float, float]] = {}
    for d, lst in by_day.items():
        lst.sort(key=lambda x: -x[0])
        closes = [c for _, c in lst[:3]]
        spread = math.log(closes[0] / closes[1]) if len(closes) >= 2 else 0.0
        slope = math.log(closes[1] / closes[2]) if len(closes) >= 3 else 0.0
        out[d] = (spread, slope)
    _cache[key] = (now, out)
    return out


def attach_term_structure(
    bars: list[dict[str, Any]], spread_by_day: dict[str, tuple[float, float]]
) -> None:
    """把日频期限结构按日期写进 bar 字段（近端未覆盖的日期不写字段）"""
    if not bars or not spread_by_day:
        return
    for b in bars:
        day = str(b.get("time") or "")[:10]
        vals = spread_by_day.get(day)
        if vals is not None:
            b["term_spread"] = vals[0]
            b["term_slope"] = vals[1]


async def enrich_bars_term_structure(
    bars: list[dict[str, Any]], product: str
) -> None:
    """便捷入口：按 bars 自身跨度拉期限结构并附加（日线专用）"""
    if not bars:
        return
    d_from = str(bars[0].get("time") or "")[:10]
    d_to = str(bars[-1].get("time") or "")[:10]
    if len(d_from) != 10 or len(d_to) != 10:
        return
    spread = await fetch_term_spread_daily(product, d_from, d_to)
    attach_term_structure(bars, spread)
