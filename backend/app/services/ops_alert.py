"""运维告警事件流：记录行情链路事件，供 admin 查看 + 实时推在线 admin。

照搬 kline:sync:log（Redis List）+ ws:big-order（Redis pub/sub）范式，
不落 DB notifications —— 运维告警是事件流，Redis List 留 7 天可查已足够，
且避开 notifications 表的 user_id 复杂度。

事件源（source）：
- simnow_fallback   SimNow 主源失效，回退新浪/东财兜底
- bridge_self_heal  bridge tick 停滞自愈重启
- channel_switch    live 渠道切换
- bridge_down       bridge 容器异常退出（崩溃/非自愈）

数据流：
  事件源 ──► push_ops_alert ──► LPUSH market:alert:log（留 200 条 / TTL 7d）
                                  SET market:alert:last
                                  PUBLISH ws:ops-alert ──► ws 订阅 ──► 推在线 admin
  admin 页 ── read_ops_alerts(LRANGE) ── /api/admin/ops-alerts

去重：同 source 在 dedupe_sec 内只记一条（防主源失效每秒刷屏）。
bridge 进程无法直发（独立容器、无 DB），自愈 os._exit 前写
market:{mode}:bridge:last_exit，由 backend channel_health_monitor_loop 检测后代发。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.ops_alert")

# Redis 键
ALERT_LOG_KEY = "market:alert:log"  # List，LPUSH，留最近 N 条
ALERT_LAST_KEY = "market:alert:last"  # string，最新一条
ALERT_CHANNEL = "ws:ops-alert"  # pub/sub，实时推在线 admin
ALERT_TTL = 7 * 24 * 3600  # 7 天
_ALERT_MAX = 200  # 保留最近 200 条

VALID_LEVELS = {"info", "warning", "error", "critical"}


def bridge_last_exit_key(mode: str) -> str:
    """bridge 自愈退出前写的 key（backend 检测 ts 变化后代发告警）"""
    return f"market:{mode}:bridge:last_exit"


async def push_ops_alert(
    redis_conn: aioredis.Redis | None,
    level: str,
    source: str,
    title: str,
    detail: Any = None,
    dedupe_sec: int = 300,
) -> dict[str, Any] | None:
    """记录一条运维告警事件 + 实时推送在线 admin。

    - level: info / warning / error / critical
    - source: simnow_fallback / bridge_self_heal / channel_switch / bridge_down
    - title: 简短标题（admin 列表展示）
    - detail: 任意 JSON 可序列化结构（展开详情）
    - dedupe_sec: 同 source 在此秒数内不重复（0 = 不去重）；默认 300s 防刷屏

    返回写入的 entry；被去重跳过或 Redis 异常时返回 None。
    """
    if redis_conn is None:
        return None
    lvl = level if level in VALID_LEVELS else "info"

    # 去重：SET NX（同 source 在 dedupe_sec 内只过一次）
    if dedupe_sec and dedupe_sec > 0:
        dedupe_key = f"market:alert:dedupe:{source}"
        try:
            ok = await redis_conn.set(dedupe_key, "1", nx=True, ex=int(dedupe_sec))
        except Exception:
            logger.debug("写告警去重 key 失败 source=%s", source, exc_info=True)
            ok = True  # Redis 异常不阻塞告警本身
        if not ok:
            return None  # 去重窗口内，跳过

    entry = {
        "id": uuid.uuid4().hex,
        "ts": time.time(),
        "level": lvl,
        "source": source,
        "title": title,
        "detail": detail,
    }
    payload = json.dumps(entry, ensure_ascii=False)
    try:
        pipe = redis_conn.pipeline()
        pipe.lpush(ALERT_LOG_KEY, payload)
        pipe.ltrim(ALERT_LOG_KEY, 0, _ALERT_MAX - 1)
        pipe.expire(ALERT_LOG_KEY, ALERT_TTL)
        pipe.set(ALERT_LAST_KEY, payload, ex=ALERT_TTL)
        await pipe.execute()
    except Exception:
        logger.debug("写 ops_alert 事件流失败 source=%s", source, exc_info=True)
        return None

    # 实时推在线 admin（跨 worker pub/sub，ws.py 订阅 ALERT_CHANNEL）
    try:
        await redis_conn.publish(
            ALERT_CHANNEL,
            json.dumps({"type": "ops_alert", "data": entry}, ensure_ascii=False),
        )
    except Exception:
        logger.debug("发布 ops_alert 频道失败 source=%s", source, exc_info=True)
    return entry


async def read_ops_alerts(
    redis_conn: aioredis.Redis | None, limit: int = 200
) -> list[dict[str, Any]]:
    """读最近 limit 条告警事件（最新在前，供 admin 页面展示）"""
    if redis_conn is None:
        return []
    try:
        raw = await redis_conn.lrange(ALERT_LOG_KEY, 0, max(0, limit - 1))
    except Exception:
        logger.debug("读 ops_alert 事件流失败", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    return out
