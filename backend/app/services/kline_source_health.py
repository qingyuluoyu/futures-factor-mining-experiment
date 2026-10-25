"""K 线历史数据源状态监控 —— 主动探测 + Redis 缓存

仿实时行情 channel_health 的模式：后台周期性对每个 K 线源
（sina/simnow）发一次轻量请求（1 根 bar），测量响应耗时 +
是否返回有效数据，输出 green/orange/red 三态，写 Redis 供管理后台展示。

与运行时降级（kline_provider.fetch_kline_with_fallback）分离：
- 探测只读 kline:source:fails / cooldown 展示，不写这些键（避免污染降级计数）；
- 探测结果单独写 kline:source:health:{source}（TTL 120s）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.kline_source_health")

# 健康键前缀（与 kline_source_cfg 的 config 键并列）
HEALTH_KEY_PREFIX = "kline:source:health"
_HEALTH_TTL = 200  # 需 > _PROBE_INTERVAL，任务停了 200s 键过期 = red

# 探测间隔（原 30s 会每 30s 新建一个 tqsdk WSS，制造重连日志噪声；降到 120s 兼顾新鲜度）
_PROBE_INTERVAL = 120

# 三态判定阈值（毫秒）
_LATENCY_GREEN = 3000  # ≤ 3s = green
_LATENCY_ORANGE = 8000  # ≤ 8s = orange；> 8s 或无数据 = red

# 探测样本合约（螺纹钢主力，流动性高、各源都覆盖）
_PROBE_SYMBOL = "rb2610"
_PROBE_PERIOD = "1d"


def _health_key(source: str) -> str:
    return f"{HEALTH_KEY_PREFIX}:{source}"


async def _get_str(redis_conn: aioredis.Redis | None, key: str) -> str | None:
    if redis_conn is None:
        return None
    try:
        raw = await redis_conn.get(key)
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        return None


def _fetch_one(source: str, symbol: str, period: str) -> list[dict[str, Any]]:
    """同步调用单源 fetcher（底层，不经过 fallback 调度）

    tqsdk/vvtr 已从取数链移除，不再探测。
    """
    from app.data import kline_provider as kp

    if source == "sina":
        return kp._fetch_sina(symbol, period)
    if source == "simnow":
        return kp._fetch_simnow(symbol, period)
    return []


async def probe_kline_source(
    redis_conn: aioredis.Redis | None,
    source: str,
    symbol: str = _PROBE_SYMBOL,
    period: str = _PROBE_PERIOD,
) -> dict[str, Any]:
    """探测单个 K 线源 → 健康状态 dict

    返回结构（与 ChannelHealth 风格对齐）：
        {status, source, latency_ms, has_data, last_bar_date, fails, in_cooldown, reason}
    """
    from app.data import kline_provider as kp

    # simnow 读 PG，分钟线数据最及时（当天实时写入）；日线可能没有当天数据
    if source == "simnow" and period == "1d":
        period = "1m"

    # 读运行时失败计数 / 冷却（只读，不写）
    fails_raw = await _get_str(redis_conn, kp._fails_key(source))
    fails = int(fails_raw) if fails_raw and fails_raw.isdigit() else 0
    in_cooldown = await kp._in_cooldown(redis_conn, source) if redis_conn else False

    base: dict[str, Any] = {
        "source": source,
        "latency_ms": None,
        "has_data": False,
        "last_bar_date": None,
        "fails": fails,
        "in_cooldown": in_cooldown,
        "reason": None,
    }

    loop = asyncio.get_event_loop()
    start = time.monotonic()
    try:
        bars = await loop.run_in_executor(
            None, _fetch_one, source, symbol, period
        )
    except asyncio.TimeoutError:
        latency = int((time.monotonic() - start) * 1000)
        base["latency_ms"] = latency
        base["status"] = "red"
        base["reason"] = "探测超时"
        return base
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        base["latency_ms"] = latency
        base["status"] = "red"
        base["reason"] = f"探测异常: {type(exc).__name__}"
        return base

    latency = int((time.monotonic() - start) * 1000)
    base["latency_ms"] = latency

    if not bars:
        base["status"] = "red"
        base["reason"] = "无数据"
        return base

    # 有效性：取最新 bar 的 time/date
    last_bar = bars[-1] if isinstance(bars, list) and bars else {}
    last_date = str(last_bar.get("time") or "").split(" ")[0] or None
    base["has_data"] = True
    base["last_bar_date"] = last_date

    if latency <= _LATENCY_GREEN:
        base["status"] = "green"
    elif latency <= _LATENCY_ORANGE:
        base["status"] = "orange"
    else:
        base["status"] = "red"
        base["reason"] = f"响应过慢({latency}ms)"
    return base


async def write_kline_source_health_to_redis(
    redis_conn: aioredis.Redis | None,
    healths: dict[str, dict[str, Any]],
) -> None:
    """各源健康写 Redis（TTL 120s）"""
    if redis_conn is None:
        return
    try:
        pipe = redis_conn.pipeline()
        for src, h in healths.items():
            pipe.set(_health_key(src), json.dumps(h, ensure_ascii=False), ex=_HEALTH_TTL)
        await pipe.execute()
    except Exception:
        logger.debug("写 kline:source:health 失败", exc_info=True)


async def collect_kline_sources_health(
    redis_conn: aioredis.Redis | None,
    sources: list[str],
) -> dict[str, dict[str, Any]]:
    """探测多个源，返回 {source: health}"""
    healths: dict[str, dict[str, Any]] = {}
    for src in sources:
        try:
            healths[src] = await probe_kline_source(redis_conn, src)
        except Exception:
            logger.exception("K 线源 %s 探测异常", src)
            healths[src] = {
                "source": src,
                "status": "red",
                "latency_ms": None,
                "has_data": False,
                "last_bar_date": None,
                "fails": 0,
                "in_cooldown": False,
                "reason": "探测异常",
            }
    return healths


async def kline_source_health_monitor_loop(
    redis_conn: aioredis.Redis,
    session_factory: Any,
) -> None:
    """后台监控循环：周期性探测当前配置的主+备 K 线源，写 Redis

    session_factory 用于读 DB 配置（主/备渠道列表）。
    """
    from app.services.kline_source_cfg import get_kline_source_config

    await asyncio.sleep(8)  # 错开启动峰值
    while True:
        try:
            # 从 DB 读当前主/备配置
            from sqlalchemy.ext.asyncio import AsyncSession

            async with session_factory() as session:
                primary, backups = await get_kline_source_config(redis_conn, session)
            # 去重：主 + 备
            seen: set[str] = set()
            to_probe: list[str] = []
            for src in [primary, *backups]:
                if src and src not in seen:
                    seen.add(src)
                    to_probe.append(src)
            healths = await collect_kline_sources_health(redis_conn, to_probe)
            await write_kline_source_health_to_redis(redis_conn, healths)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("K 线源健康监控循环异常")
        await asyncio.sleep(_PROBE_INTERVAL)
