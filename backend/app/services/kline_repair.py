"""启动对账修正 —— 主 PG 权威数据修正 Redis K 线缓存

桌面端启动时触发：近 N 天主力合约 × 全周期，用主 PG（收盘权威修正
kline_sync + 实时固化 persist_closed_bar 的沉淀库）对账 Redis 历史缓存：
重叠时间键 OHLCV 有实质差异 → PG 值覆盖；缓存缺已收盘键 → 补齐。
幂等可重入；缓存不存在不预写（修正窗口有限，预写短序列会让客户端
拿到过短历史）；PG 侧数据经交易日历过滤（主库固化过周末幽灵，不得
回流缓存）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.kline import (
    _BJ_TZ,
    _bars_materially_differ,
    _fetch_from_pg,
    _filter_non_trading_bars,
    _get_ttl,
    _history_cache_key,
    _normalize_bars,
)
from app.services.signal_universe import list_all_master_symbols

logger = logging.getLogger("qihuo.kline_repair")

VERIFY_PERIODS = ("1m", "5m", "15m", "30m", "60m", "1d")
VERIFY_DAYS = 3


async def verify_repair_kline_cache(
    session: AsyncSession,
    redis_conn: aioredis.Redis | None,
    days: int = VERIFY_DAYS,
) -> dict[str, Any]:
    """近 N 天主力 × 全周期：PG 权威 vs Redis 缓存对账修正。

    返回统计 {symbols, checked, repaired_keys, repaired_bars, errors}。
    """
    out: dict[str, Any] = {
        "symbols": 0,
        "checked": 0,
        "repaired_keys": 0,
        "repaired_bars": 0,
        "errors": 0,
        "periods": list(VERIFY_PERIODS),
        "days": days,
    }
    if redis_conn is None:
        out["message"] = "Redis 不可用"
        return out

    symbols = await list_all_master_symbols(redis_conn)
    out["symbols"] = len(symbols)
    if not symbols:
        out["message"] = "主力合约列表为空"
        return out

    # bar 时间为北京墙钟（naive）；容器系统时区不一定是北京时间
    now_bj = datetime.now(_BJ_TZ).replace(tzinfo=None)
    cutoff = (now_bj - timedelta(days=days)).strftime("%Y-%m-%d")
    started = datetime.now()

    for item in symbols:
        symbol = str(item.get("symbol") or "").strip().lower()
        if not symbol:
            continue
        for period in VERIFY_PERIODS:
            key = _history_cache_key(symbol, period)
            try:
                raw = await redis_conn.get(key)
                if not raw:
                    continue
                cached = _normalize_bars(json.loads(raw))
                if not cached:
                    continue
                # 只对照窗口内的尾部（含今日已收盘/盘前历史）
                window = [b for b in cached if str(b.get("time") or "") >= cutoff]
                if not window:
                    continue
                out["checked"] += 1

                pg_bars = await _fetch_from_pg(session, symbol, period)
                pg_bars = _filter_non_trading_bars(pg_bars, period)
                if not pg_bars:
                    continue
                pg_by_time = {
                    str(b.get("time")): b
                    for b in pg_bars
                    if str(b.get("time") or "") >= cutoff
                }
                if not pg_by_time:
                    continue

                by_time = {str(b.get("time") or ""): b for b in cached}
                diff = 0
                for t, pg_bar in pg_by_time.items():
                    cur = by_time.get(t)
                    if cur is None:
                        # 缓存缺已收盘键（源丢根）→ 补 PG 权威值
                        by_time[t] = pg_bar
                        diff += 1
                    elif _bars_materially_differ(cur, pg_bar):
                        by_time[t] = pg_bar
                        diff += 1
                if not diff:
                    continue

                merged = [by_time[k] for k in sorted(by_time)]
                await redis_conn.set(
                    key,
                    json.dumps(merged, ensure_ascii=False),
                    ex=_get_ttl(period),
                )
                out["repaired_keys"] += 1
                out["repaired_bars"] += diff
            except Exception:
                out["errors"] += 1
                logger.debug(
                    "对账修正失败 %s %s", symbol, period, exc_info=True
                )

    out["elapsed_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    out["message"] = (
        f"对账完成：{out['symbols']} 合约 × {len(VERIFY_PERIODS)} 周期，"
        f"检查 {out['checked']} 项，修正 {out['repaired_keys']} 项 / "
        f"{out['repaired_bars']} 根，错误 {out['errors']}"
    )
    logger.info("启动对账修正：%s", out["message"])
    return out
