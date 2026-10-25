"""交易时段刷新间隔运行时配置 —— 默认值 + per-channel 覆盖

管理后台（PUT /api/admin/settings）保存
system_settings.market_refresh_interval_sec（默认）与
market_refresh_intervals（per-channel JSON）后，同步写 Redis 镜像键
runtime:refresh_interval；运行时 scheduler._background_fetch_loop 与
ws._poll_loop 读当前 active 渠道，用对应间隔。

取值优先级：per-channel[channel] > default > 代码默认（1 秒）。
休市间隔（_FETCH_INTERVAL_IDLE=60）不受此配置影响。

Redis 镜像存扁平 JSON：{"default":1, "sina":3, "simnow":1, ...}
兼容旧格式 {"sec":N}（视作默认值）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.refresh_cfg")

CONFIG_KEY = "runtime:refresh_interval"
_CONFIG_TTL = 300

DEFAULT_INTERVAL = 5
MIN_INTERVAL = 1
MAX_INTERVAL = 10


def _clamp(sec: Any) -> int:
    """钳位到 [MIN_INTERVAL, MAX_INTERVAL]，防越界"""
    try:
        return max(MIN_INTERVAL, min(MAX_INTERVAL, int(sec)))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL


async def _read_map(redis_conn: aioredis.Redis | None) -> dict[str, int]:
    """读 Redis 镜像，返回扁平 dict（含 default + per-channel）。miss 返回 {}"""
    if redis_conn is None:
        return {}
    try:
        raw = await redis_conn.get(CONFIG_KEY)
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        # 兼容旧格式 {"sec": N} → 视作默认值
        if "sec" in payload and "default" not in payload:
            return {"default": _clamp(payload["sec"])}
        result: dict[str, int] = {}
        for k, v in payload.items():
            try:
                result[str(k)] = _clamp(v)
            except (TypeError, ValueError):
                continue
        return result
    except Exception:
        logger.debug("读 %s 失败", CONFIG_KEY, exc_info=True)
        return {}


async def get_refresh_interval(
    redis_conn: aioredis.Redis | None,
    channel: str | None = None,
) -> int:
    """返回刷新间隔秒数。

    优先级：per-channel[channel] > default > 代码默认（1）。
    channel=None 时返回默认值。
    """
    data = await _read_map(redis_conn)
    if channel and channel in data:
        return data[channel]
    return data.get("default", DEFAULT_INTERVAL)


async def write_refresh_intervals_to_redis(
    redis_conn: aioredis.Redis | None,
    default_sec: Any,
    intervals_map: dict[str, int] | None,
) -> None:
    """admin PUT 保存后同步镜像。

    存扁平 JSON：{"default": default_sec, "<channel>": sec, ...}
    """
    if redis_conn is None:
        return
    payload: dict[str, int] = {"default": _clamp(default_sec)}
    if isinstance(intervals_map, dict):
        for k, v in intervals_map.items():
            payload[str(k)] = _clamp(v)
    try:
        await redis_conn.set(
            CONFIG_KEY,
            json.dumps(payload),
            ex=_CONFIG_TTL,
        )
    except Exception:
        logger.debug("写 %s 失败", CONFIG_KEY, exc_info=True)
