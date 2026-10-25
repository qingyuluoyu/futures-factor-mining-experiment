"""K 线修正任务组状态助手 —— 统一心跳/日志/暂停开关(Redis)

沿用 kline_sync 的既有模式(kline:sync:status STRING + kline:sync:log LIST),
推广到 K 线修正组全部任务。键格式:

    kline:task:{name}:status   STRING JSON(最新快照,读-合并-写)
    kline:task:{name}:log      LIST(JSON 头插,LTRIM 保留 200 条)
    kline:task:{name}:paused   STRING "1"(存在即暂停)

全部 best-effort:Redis 异常吞掉,绝不影响任务主流程。
kline_sync 的旧键(kline:sync:status|log)保留不动,read_* 兼容读取。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.kline_task")

_BJ_TZ = timezone(timedelta(hours=8))

_LOG_KEEP = 200

# K 线修正组任务注册表(聚合展示与控制白名单)
# pausable: 可 pause/resume;runnable: 可手动触发
KLINE_TASKS: dict[str, dict[str, Any]] = {
    "kline_sync": {
        "label": "K线权威修正(pg-tickdata)",
        "interval_desc": "每日 02:00 + 15:30",
        "pausable": True,
        "runnable": True,
    },
    "kline_reconcile": {
        "label": "K线盘中对账",
        "interval_desc": "交易时段每 10 分钟",
        "pausable": True,
        "runnable": True,
    },
    "post_deploy_reconcile": {
        "label": "升级后一次性恢复(治愈恢复态forming)",
        "interval_desc": "启动后 2 分钟(一次性)",
        "pausable": False,
        "runnable": False,
    },
    "vvtr_tickdata_sync": {
        "label": "VVTR→pg-tickdata 主力同步",
        "interval_desc": "盘中每小时/休市6小时 + 每日01:00/15:05定点",
        "pausable": True,
        "runnable": True,
    },
    "tickdata_gap_backfill": {
        "label": "tickdata 缺口定向回填",
        "interval_desc": "每日 03:30",
        "pausable": True,
        "runnable": True,
    },
}


def _now_iso() -> str:
    return datetime.now(_BJ_TZ).replace(tzinfo=None).isoformat(timespec="seconds")


def _status_key(name: str) -> str:
    # kline_sync 沿用历史键(channels 页/旧接口仍读)
    return "kline:sync:status" if name == "kline_sync" else f"kline:task:{name}:status"


def _log_key(name: str) -> str:
    # kline_sync 沿用历史键(channels 页/旧接口仍读)
    return "kline:sync:log" if name == "kline_sync" else f"kline:task:{name}:log"


def _paused_key(name: str) -> str:
    return f"kline:task:{name}:paused"


async def report(redis_conn: aioredis.Redis | None, name: str, **fields: Any) -> None:
    """合并写任务状态快照(保留既有字段);异常静默"""
    if redis_conn is None:
        return
    try:
        key = _status_key(name)
        raw = await redis_conn.get(key)
        data: dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                data = {}
        data.update(fields)
        await redis_conn.set(key, json.dumps(data, ensure_ascii=False))
    except Exception:
        logger.debug("任务状态上报失败 %s", name, exc_info=True)


async def log(
    redis_conn: aioredis.Redis | None,
    name: str,
    level: str,
    msg: str,
    **data: Any,
) -> None:
    """追加一条任务日志(头插,裁剪保留 _LOG_KEEP 条);异常静默"""
    if redis_conn is None:
        return
    try:
        entry = {"ts": _now_iso(), "level": level, "msg": msg, **data}
        key = _log_key(name)
        pipe = redis_conn.pipeline()
        pipe.lpush(key, json.dumps(entry, ensure_ascii=False))
        pipe.ltrim(key, 0, _LOG_KEEP - 1)
        await pipe.execute()
    except Exception:
        logger.debug("任务日志写入失败 %s", name, exc_info=True)


async def is_paused(redis_conn: aioredis.Redis | None, name: str) -> bool:
    """任务是否被管理端暂停;Redis 异常视为未暂停(不阻断任务)"""
    if redis_conn is None:
        return False
    try:
        return bool(await redis_conn.exists(_paused_key(name)))
    except Exception:
        return False


async def set_paused(redis_conn: aioredis.Redis, name: str, paused: bool) -> None:
    """设置/清除暂停标志"""
    key = _paused_key(name)
    if paused:
        await redis_conn.set(key, "1")
    else:
        await redis_conn.delete(key)


async def read_status(redis_conn: aioredis.Redis, name: str) -> dict[str, Any]:
    """读任务状态;kline_sync 兼容读历史键,失败返回空"""
    try:
        key = "kline:sync:status" if name == "kline_sync" else _status_key(name)
        raw = await redis_conn.get(key)
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def read_paused(redis_conn: aioredis.Redis, name: str) -> bool:
    try:
        return bool(await redis_conn.exists(_paused_key(name)))
    except Exception:
        return False


async def read_logs(
    redis_conn: aioredis.Redis, name: str, limit: int = 100
) -> list[dict[str, Any]]:
    """读任务日志(新在前);失败返回空"""
    try:
        raw_list = await redis_conn.lrange(_log_key(name), 0, max(0, limit) - 1)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_list:
        try:
            item = json.loads(raw)
            if isinstance(item, dict):
                out.append(item)
        except (json.JSONDecodeError, TypeError):
            continue
    return out
