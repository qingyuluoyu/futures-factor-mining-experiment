"""虚拟盘历史 K 线库 —— Redis 独立命名空间

策略：
- 底图：新浪历史（source=seed），保证 1m~30m 有完整可画序列
- 增量：openctp 收盘 bar（source=virtual）仅追加在种子最后时间之后
- forming：kline:virtual:realtime 合并到尾部
- 历史过薄或缺少 seed 时自动重新种子（避免清缓存后只剩十几根）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.services.kline_keys import history_kline_key, realtime_kline_key

logger = logging.getLogger("qihuo.kline_virtual_hist")

_MAX_BARS: dict[str, int] = {
    "1m": 2000,
    "5m": 1500,
    "15m": 1200,
    "30m": 1000,
    "60m": 800,
    "1d": 2000,
}

# 少于此数量视为「过薄」，强制重新种子
_MIN_BARS: dict[str, int] = {
    "1m": 200,
    "5m": 100,
    "15m": 80,
    "30m": 60,
    "60m": 40,
    "1d": 30,
}

_HISTORY_TTL = 90 * 24 * 3600


def _bar_time_key(period: str, time_str: str) -> str:
    t = str(time_str or "").strip()
    if period == "1d":
        return t[:10]
    return t


def _normalize_bar(bar: dict[str, Any]) -> dict[str, Any]:
    out = dict(bar)
    if "settle" not in out:
        out["settle"] = None
    if "open_interest" not in out:
        out["open_interest"] = None
    return out


def _dedupe(bars: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    if not bars:
        return bars
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for bar in bars:
        key = _bar_time_key(period, str(bar.get("time") or ""))
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = bar
    return [by_key[k] for k in order]


def _has_seed(bars: list[dict[str, Any]]) -> bool:
    return any(str(b.get("source") or "") == "seed" for b in bars)


def _needs_reseed(bars: list[dict[str, Any]], period: str) -> bool:
    if not bars:
        return True
    if not _has_seed(bars):
        return True
    return len(bars) < _MIN_BARS.get(period, 50)


async def load_virtual_history(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
) -> list[dict[str, Any]]:
    key = history_kline_key("virtual", symbol, period)
    try:
        raw = await redis_conn.get(key)
    except Exception:
        logger.exception("读 virtual 历史失败 %s %s", symbol, period)
        return []
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [_normalize_bar(b) for b in data if isinstance(b, dict)]


async def save_virtual_history(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    bars: list[dict[str, Any]],
) -> None:
    cap = _MAX_BARS.get(period, 1000)
    cleaned = _dedupe([_normalize_bar(b) for b in bars], period)
    if len(cleaned) > cap:
        cleaned = cleaned[-cap:]
    key = history_kline_key("virtual", symbol, period)
    payload = json.dumps(cleaned, ensure_ascii=False)
    if _HISTORY_TTL > 0:
        await redis_conn.set(key, payload, ex=_HISTORY_TTL)
    else:
        await redis_conn.set(key, payload)


async def append_closed_bar(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    bar: dict[str, Any],
) -> None:
    """归档已收盘 virtual bar（不覆盖 seed 同键：virtual 优先）"""
    if not bar or not bar.get("time"):
        return
    closed = _normalize_bar(dict(bar))
    closed["source"] = "virtual"
    bars = await load_virtual_history(redis_conn, symbol, period)
    tkey = _bar_time_key(period, str(closed.get("time") or ""))
    if not tkey:
        return
    # 若历史过薄，先种子再追加，避免长期只有几根
    if _needs_reseed(bars, period):
        bars = await seed_virtual_history_from_sina(
            redis_conn, symbol, period, preserve_virtual=bars
        )
    replaced = False
    for i, b in enumerate(bars):
        if _bar_time_key(period, str(b.get("time") or "")) == tkey:
            bars[i] = closed
            replaced = True
            break
    if not replaced:
        bars.append(closed)
    await save_virtual_history(redis_conn, symbol, period, bars)


async def seed_virtual_history_from_sina(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    preserve_virtual: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """用多数据源历史重建底图，保留种子时间之后的 virtual 增量"""
    from app.data.kline_provider import fetch_kline_with_fallback

    # 失败节流：seed 失败后 60s 内不再重试（虚拟盘归档循环每秒触发，
    # 若种子持续失败会高频打数据源）
    fail_key = f"kline:virtual:seed:fail:{symbol}:{period}"
    try:
        if await redis_conn.get(fail_key):
            return list(preserve_virtual or [])
    except Exception:
        pass

    try:
        # 主备模式默认源顺序（主=东财 → 备=新浪），主渠道失败自动降级
        raw_bars = await fetch_kline_with_fallback(
            redis_conn, symbol, period
        )
    except Exception:
        logger.exception("virtual 历史种子拉取失败 %s %s", symbol, period)
        return list(preserve_virtual or [])

    if not raw_bars:
        try:
            await redis_conn.set(fail_key, "1", ex=60)
        except Exception:
            pass
        return list(preserve_virtual or [])

    seeded: list[dict[str, Any]] = []
    for b in raw_bars:
        if not isinstance(b, dict):
            continue
        nb = _normalize_bar(dict(b))
        nb["source"] = "seed"
        seeded.append(nb)
    seeded = _dedupe(seeded, period)
    if not seeded:
        return list(preserve_virtual or [])

    last_seed_t = _bar_time_key(period, str(seeded[-1].get("time") or ""))
    kept: list[dict[str, Any]] = []
    for b in preserve_virtual or []:
        if str(b.get("source") or "") != "virtual":
            continue
        t = _bar_time_key(period, str(b.get("time") or ""))
        # 只保留种子末根之后的 virtual（7×24 盘中增量）
        if t and last_seed_t and t > last_seed_t:
            kept.append(_normalize_bar(dict(b)))

    merged = _dedupe(seeded + kept, period)
    await save_virtual_history(redis_conn, symbol, period, merged)
    logger.info(
        "virtual 历史已种子 %s %s seed=%d virtual_keep=%d total=%d",
        symbol,
        period,
        len(seeded),
        len(kept),
        len(merged),
    )
    return merged


async def _load_forming_bar(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
) -> dict[str, Any] | None:
    key = realtime_kline_key("virtual", symbol, period)
    try:
        raw = await redis_conn.get(key)
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        bar = payload.get("bar") if isinstance(payload, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(bar, dict) or not bar.get("time"):
        return None
    return _normalize_bar(dict(bar))


def _merge_forming(
    bars: list[dict[str, Any]],
    forming: dict[str, Any],
    period: str,
) -> list[dict[str, Any]]:
    rt_key = _bar_time_key(period, str(forming.get("time") or ""))
    if not rt_key:
        return bars
    if not bars:
        return [forming]
    last_key = _bar_time_key(period, str(bars[-1].get("time") or ""))
    if rt_key == last_key:
        # 同键：seed 底图上用 virtual last 更新 C/H/L，保留 seed open 更稳
        merged = dict(bars[-1])
        prev_c = float(merged.get("close") or 0)
        new_c = float(forming.get("close") or 0)
        # 若 forming 与底图价格偏离过大（>3%），只改 close 到 forming，重算 h/l 限幅
        # 正常合并 forming 的 H/L/C；大幅偏离时仍用 forming 值（同合约同桶）
        for field in ("high", "low", "close", "volume", "settle", "open_interest"):
            if forming.get(field) is not None:
                merged[field] = forming[field]
        _ = prev_c
        _ = new_c
        merged["source"] = forming.get("source") or "virtual"
        bars[-1] = _normalize_bar(merged)
        return bars
    if rt_key > last_key:
        bars.append(forming)
        return bars
    # forming 时间早于历史末根（种子含更晚实盘 bar）：不插入，避免打乱
    return bars


async def get_virtual_kline_history(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    limit: int = 0,
    end_time: str = "",
) -> list[dict[str, Any]]:
    """virtual 完整历史：种子底图 + virtual 增量 + forming"""
    sym = symbol.strip().lower()
    bars = await load_virtual_history(redis_conn, sym, period)
    if _needs_reseed(bars, period):
        bars = await seed_virtual_history_from_sina(
            redis_conn, sym, period, preserve_virtual=bars
        )

    if end_time:
        bars = [b for b in bars if str(b.get("time") or "") < end_time]
    if limit > 0:
        bars = bars[-limit:]

    if not end_time:
        forming = await _load_forming_bar(redis_conn, sym, period)
        if forming is not None:
            bars = _merge_forming(list(bars), forming, period)

    return _dedupe(bars, period)
