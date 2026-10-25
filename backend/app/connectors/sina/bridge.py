"""sina-bridge 轮询循环 —— 新浪 HTTP 的唯一出网点

三个常驻任务（由 api.lifespan / main 启动）：
- quote 轮询：fetch_sina_quotes → filter_valid_quotes + recv_ts 盖戳
  → 源键 market:live:source:sina:quote:{sym}（TTL 7d）。失败轮跳写，
  陈旧度由键内 recv_ts 表达（merge 层 90s freshness 判定）。
- 月度全合约：全月份合约 quote → market:monthly:quote:*（TTL 120s，
  原 scheduler._background_monthly_quotes 循环整体搬入）。
- 心跳：market:live:sina:bridge:heartbeat（TTL 120s，仿 ctp-bridge）。

轮询节奏自适应：推送源（vvtr WS 桥）实际供数时降到
POLL_INTERVAL_PUSH_ACTIVE（只补昨结/名称等日内静态字段，避免 3s 全量
拉把行情主循环拖慢——2026-08-20 实测）；否则交易时段秒级、休市降频。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from . import config

logger = logging.getLogger("qihuo.sina_bridge")

_BJ_TZ = timezone(timedelta(hours=8))

# 源键前缀（与 market_keys.source_quote_key("sina", ...) 一致；
# 此处独立拼接避免与 backend 服务层耦合）
_SOURCE_KEY_PREFIX = "market:live:source:sina:quote:"
_MONTHLY_KEY_PREFIX = "market:monthly:quote:"

# 判定推送源供数的心跳新鲜度阈值（vvtr 心跳键 TTL 120s）
_PUSH_HEARTBEAT_MAX_AGE = 120.0


class BridgeState:
    """桥运行状态（/health、/stats 与轮询循环共享）"""

    def __init__(self) -> None:
        self.latest_quotes: list[dict[str, Any]] = []
        self.last_poll_ts: float = 0.0
        self.last_poll_ok: bool = False
        self.last_poll_error: str = ""
        self.poll_count: int = 0
        self.poll_fail: int = 0
        self.last_monthly_ts: float = 0.0
        self.last_monthly_count: int = 0
        self.interval_mode: str = "init"  # trading / closed / push-active
        self.replica_enabled: bool = bool(config.REPLICATE_REDIS_URL)
        self.last_replica_ok: bool | None = None
        self.last_replica_error: str = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "last_poll_ts": self.last_poll_ts,
            "last_poll_age": round(time.time() - self.last_poll_ts, 1)
            if self.last_poll_ts
            else None,
            "last_poll_ok": self.last_poll_ok,
            "last_poll_error": self.last_poll_error,
            "poll_count": self.poll_count,
            "poll_fail": self.poll_fail,
            "cached_quotes": len(self.latest_quotes),
            "last_monthly_ts": self.last_monthly_ts,
            "last_monthly_count": self.last_monthly_count,
            "interval_mode": self.interval_mode,
            "replica_enabled": self.replica_enabled,
            "last_replica_ok": self.last_replica_ok,
            "last_replica_error": self.last_replica_error,
        }


# 推流副本连接（懒创建；58 中心化部署时双写 33 的 Redis）
_replica_redis: aioredis.Redis | None = None


def _get_replica() -> aioredis.Redis | None:
    global _replica_redis
    if not config.REPLICATE_REDIS_URL:
        return None
    if _replica_redis is None:
        _replica_redis = aioredis.from_url(
            config.REPLICATE_REDIS_URL, decode_responses=True
        )
    return _replica_redis


async def _mark_replica(state: BridgeState, exc: Exception | None) -> None:
    if exc is None:
        if state.last_replica_ok is not True:
            state.last_replica_ok = True
            state.last_replica_error = ""
    else:
        state.last_replica_ok = False
        state.last_replica_error = f"{type(exc).__name__}: {exc}"
        logger.warning("推流副本写入失败：%s", state.last_replica_error)


async def close_replica() -> None:
    """关闭推流副本连接（进程退出时调用）"""
    global _replica_redis
    if _replica_redis is not None:
        try:
            await _replica_redis.aclose()
        except Exception:
            pass
        _replica_redis = None


async def _write_sets(
    redis_conn: aioredis.Redis,
    sets: list[tuple[str, str, int | None]],
    state: BridgeState | None = None,
) -> None:
    """写主 Redis，并按配置双写推流副本（副本失败不影响主链路）"""
    pipe = redis_conn.pipeline()
    for key, value, ex in sets:
        pipe.set(key, value, ex=ex)
    await pipe.execute()

    replica = _get_replica()
    if replica is None:
        return
    try:
        rpipe = replica.pipeline()
        for key, value, ex in sets:
            rpipe.set(key, value, ex=ex)
        await rpipe.execute()
        if state is not None:
            await _mark_replica(state, None)
    except Exception as exc:
        if state is not None:
            await _mark_replica(state, exc)


async def _push_source_active(redis_conn: aioredis.Redis) -> bool:
    """推送源（vvtr WS 桥）是否在实际供数：渠道=vvtr 且开关开且心跳新鲜"""
    from app.connectors.vvtr.config import read_vvtr_state_from_redis
    from app.connectors.vvtr.ws_bridge import VVTR_HEARTBEAT_KEY

    from app.services.market_channel import CHANNEL_VVTR, read_channel_from_redis

    try:
        channel = await read_channel_from_redis(redis_conn)
        if channel != CHANNEL_VVTR:
            return False
        state = await read_vvtr_state_from_redis(redis_conn)
        if not (state.get("enabled") and state.get("quotes_enabled")):
            return False
        raw = await redis_conn.get(VVTR_HEARTBEAT_KEY)
        if not raw:
            return False
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        # 心跳 payload 为 JSON（含 ts）；兼容裸时间戳格式
        ts: float | None = None
        try:
            payload = json.loads(raw)
            ts = float(payload.get("ts") or payload.get("time") or 0) or None
        except (ValueError, TypeError):
            try:
                ts = float(raw)
            except ValueError:
                ts = None
        if ts is None:
            # payload 无法解析时间：键存在（TTL 120s 未过期）即视为供数
            return True
        return time.time() - ts < _PUSH_HEARTBEAT_MAX_AGE
    except Exception:
        return False


async def _current_interval(redis_conn: aioredis.Redis) -> tuple[float, str]:
    """当前轮询间隔与模式"""
    from app.services.trading_hours import is_trading_hours

    if await _push_source_active(redis_conn):
        return config.POLL_INTERVAL_PUSH_ACTIVE, "push-active"
    if is_trading_hours():
        return config.POLL_INTERVAL_TRADING, "trading"
    return config.POLL_INTERVAL_CLOSED, "closed"


async def poll_once(
    redis_conn: aioredis.Redis,
    state: BridgeState,
) -> int:
    """拉一轮新浪 quote 并写源键；返回写入条数（失败返回 0 且跳写）"""
    import asyncio

    from app.data.quote_schema import filter_valid_quotes
    from app.data.sina_quote import fetch_sina_quotes

    poll_ts = time.time()
    try:
        raw = await asyncio.to_thread(fetch_sina_quotes)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.poll_count += 1
        state.poll_fail += 1
        state.last_poll_ok = False
        state.last_poll_error = f"{type(exc).__name__}: {exc}"
        logger.warning("新浪行情拉取异常：%s", state.last_poll_error)
        return 0

    quotes = filter_valid_quotes(raw)
    state.poll_count += 1
    state.last_poll_ts = poll_ts
    state.last_poll_error = ""
    if not quotes:
        # 空结果（限流/接口异常）：跳写，保留旧源键，陈旧度交给 recv_ts
        state.last_poll_ok = False
        logger.warning("新浪行情拉取为空（第 %d 次失败）", state.poll_fail + 1)
        state.poll_fail += 1
        return 0
    state.last_poll_ok = True
    state.poll_fail = 0
    state.latest_quotes = quotes

    sets: list[tuple[str, str, int | None]] = []
    for q in quotes:
        q["recv_ts"] = poll_ts
        key = _SOURCE_KEY_PREFIX + str(q.get("symbol") or "")
        sets.append((key, json.dumps(q, ensure_ascii=False), config.SOURCE_KEY_TTL))
    await _write_sets(redis_conn, sets, state)
    return len(quotes)


async def quote_poll_loop(redis_conn: aioredis.Redis, state: BridgeState) -> None:
    """quote 轮询主循环（启动立即拉一次，保证部署切换窗口最短）"""
    while True:
        interval, mode = await _current_interval(redis_conn)
        state.interval_mode = mode
        try:
            n = await poll_once(redis_conn, state)
            if n:
                logger.info("新浪源键写入 %d 条（mode=%s）", n, mode)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("新浪 quote 轮询异常")
        await asyncio.sleep(interval)


async def monthly_fetch_once(
    redis_conn: aioredis.Redis,
    state: BridgeState | None = None,
) -> int:
    """拉全部月份合约 quote 写 market:monthly:quote:*（主力识别用）

    与主行情源键隔离，TTL 120s；分批 30 + 批间延迟防新浪限流。
    """
    import asyncio

    from app.data.contract_months import generate_all_month_symbols
    from app.data.sina_quote import _fetch_batch

    now = datetime.now(_BJ_TZ)
    all_months = generate_all_month_symbols(now)
    contract_map = {c["symbol"]: c for c in all_months}

    total_quotes: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    for contract in all_months:
        batch.append(contract)
        if len(batch) >= config.MONTHLY_BATCH_SIZE:
            quotes = await asyncio.to_thread(_fetch_batch, batch, contract_map)
            total_quotes.extend(quotes)
            batch = []
            await asyncio.sleep(config.MONTHLY_BATCH_DELAY)
    if batch:
        total_quotes.extend(await asyncio.to_thread(_fetch_batch, batch, contract_map))

    if not total_quotes:
        return 0
    sets = [
        (
            f"{_MONTHLY_KEY_PREFIX}{q['symbol']}",
            json.dumps(q, ensure_ascii=False),
            120,
        )
        for q in total_quotes
    ]
    await _write_sets(redis_conn, sets, state)
    return len(total_quotes)


async def monthly_fetch_loop(
    redis_conn: aioredis.Redis,
    state: BridgeState,
) -> None:
    while True:
        try:
            count = await monthly_fetch_once(redis_conn, state)
            state.last_monthly_ts = time.time()
            state.last_monthly_count = count
            logger.info("全月份合约行情拉取完成，共 %d 条", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("全月份合约行情拉取异常", exc_info=True)
        await asyncio.sleep(config.MONTHLY_INTERVAL)


async def heartbeat_loop(
    redis_conn: aioredis.Redis,
    state: BridgeState | None = None,
) -> None:
    """心跳键（compose healthcheck / channel_health 探测用）"""
    while True:
        try:
            payload = f"{time.time()}|sina-bridge"
            await _write_sets(
                redis_conn,
                [(config.HEARTBEAT_KEY, payload, int(config.HEARTBEAT_TTL))],
                state,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sina-bridge 心跳写入失败")
        await asyncio.sleep(config.HEARTBEAT_INTERVAL)
