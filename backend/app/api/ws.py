"""WebSocket 端点 —— 实时行情广播（按 JWT trading_mode 分盘）

连接时校验 JWT（query: token），解析 trading_mode，
仅推送对应命名空间的 quote/orderbook/trades；K 线仍共享新浪。
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.auth import decode_token
from app.core.config import load_settings
from app.core.trading_mode import DEFAULT_MODE, normalize_trading_mode
from app.services.market_keys import (
    orderbook_scan_pattern,
    quote_scan_pattern,
    trades_scan_pattern,
)
from app.services.channel_health import max_trade_date

logger = logging.getLogger(__name__)

router = APIRouter()

_POLL_INTERVAL_SECONDS = 1
_WS_CLOSE_UNAUTHORIZED = 4401

# 每客户端发送队列上限：满时丢最旧帧（慢消费者不背压、不拖慢同 worker 其他客户端）
_CLIENT_QUEUE_SIZE = 64

# quote 出网序列号：全局单调递增整数（epoch 毫秒基数，重启不会回退）。
# 同时按 (mode, symbol) 记录最后出网 recv_ts，回退帧（旧数据晚到，
# 如渠道切换瞬间/缓存重建竞态）在出网处拦截，所有客户端（Web/桌面）统一受益。
_SEQ_BASE = int(time.time() * 1000)
_SEQ_COUNTER = itertools.count(start=_SEQ_BASE)
_LAST_QUOTE_RECV: dict[str, float] = {}


def _stamp_and_filter_quotes(mode: str, quotes: list[dict]) -> list[dict]:
    """出网前：无效帧/回退帧丢弃 + 为每条 quote 盖单调 seq（新增字段，旧客户端兼容）"""
    from app.data.quote_schema import is_valid_quote

    out: list[dict] = []
    for q in quotes:
        if not is_valid_quote(q):
            continue  # 无 symbol / 非正价格：无效帧不出网
        sym = str(q.get("symbol") or "").strip().lower()
        try:
            rt = float(q.get("recv_ts") or 0)
        except (TypeError, ValueError):
            rt = 0.0
        key = f"{mode}:{sym}"
        last = _LAST_QUOTE_RECV.get(key)
        if rt > 0 and last is not None and rt < last:
            continue  # 回退帧：丢弃不出网
        if rt > 0:
            _LAST_QUOTE_RECV[key] = rt
        stamped = dict(q)
        stamped["seq"] = next(_SEQ_COUNTER)
        out.append(stamped)
    return out

# 通知跨 worker 广播频道：单频道，消息体带 user_id 做分发。
# 通知常在 master worker 产生（scheduler），而用户 WS 连接随机落在任一 worker，
# 进程内 _clients 互不可见 → 用 Redis pub/sub 让每个 worker 都能投递到本地连接。
_NOTIFY_CHANNEL = "ws:notify"

# 大单事件跨 worker 广播频道前缀：按 trading_mode 分频道（ws:big-order:live/virtual）
_BIG_ORDER_CHANNEL_PREFIX = "ws:big-order:"
_BIG_ORDER_CHANNEL_PATTERN = "ws:big-order:*"

# 运维告警跨 worker 广播频道（单频道，推所有在线客户端，前端 admin 监听弹窗）
_OPS_ALERT_CHANNEL = "ws:ops-alert"

_SUBSCRIBER_RETRY_INITIAL_SECONDS = 1.0
_SUBSCRIBER_RETRY_MAX_SECONDS = 30.0
_SUBSCRIBER_IO_TIMEOUT_SECONDS = 5.0
_SUBSCRIBER_CLOSE_TIMEOUT_SECONDS = 1.0


@dataclass
class _Client:
    ws: WebSocket
    trading_mode: str
    user_id: str = ""
    # K 线订阅过滤（None = 全量，兼容未升级客户端）。quote/orderbook/trades
    # 保持全市场广播：侧栏 watchlist 与大单扫描依赖全量快照，只裁最重的
    # forming K 线流（全市场 × 6 周期）。
    kline_symbols: frozenset[str] | None = None
    queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=_CLIENT_QUEUE_SIZE)
    )
    sender: asyncio.Task | None = None


_clients: list[_Client] = []
_poll_task: asyncio.Task | None = None


def _offer(client: _Client, text: str) -> None:
    """入队待发；满队丢最旧（不背压、不阻塞广播循环）"""
    try:
        client.queue.put_nowait(text)
        return
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            client.queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            client.queue.put_nowait(text)


async def _client_sender(client: _Client) -> None:
    """每客户端独立发送任务：发送失败即连接已死，标记移除交由接收端回收"""
    try:
        while True:
            text = await client.queue.get()
            await client.ws.send_text(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("客户端发送失败，连接交由接收端回收", exc_info=True)
        with contextlib.suppress(Exception):
            await client.ws.close()


def _start_sender(client: _Client) -> None:
    if client.sender is None or client.sender.done():
        client.sender = asyncio.create_task(_client_sender(client))


def _filter_klines_for(
    client: _Client, klines: list[dict]
) -> list[dict]:
    """按客户端订阅过滤 forming K 线载荷"""
    if client.kline_symbols is None:
        return klines
    return [
        k for k in klines
        if str(k.get("symbol") or "").lower() in client.kline_symbols
    ]

# 发布用的独立 Redis 连接（懒初始化，复用）；与订阅连接隔离避免阻塞互相影响
_publish_redis: aioredis.Redis | None = None
# 订阅任务（每个 worker 一个）
_subscriber_task: asyncio.Task | None = None
# 大单事件订阅任务（每个 worker 一个）
_big_order_subscriber_task: asyncio.Task | None = None
# 运维告警订阅任务（每个 worker 一个）
_ops_alert_subscriber_task: asyncio.Task | None = None


async def _get_publish_redis() -> aioredis.Redis:
    """懒初始化复用的发布连接（与 app.state.redis 独立，避免与订阅阻塞冲突）"""
    global _publish_redis
    if _publish_redis is None:
        settings = load_settings()
        _publish_redis = aioredis.from_url(
            settings.redis_url, decode_responses=True, max_connections=5
        )
    return _publish_redis


def _parse_ws_auth(token: str | None) -> tuple[bool, str, str]:
    """校验 access JWT，返回 (ok, trading_mode, user_id)"""
    if not token:
        return False, DEFAULT_MODE, ""
    settings = load_settings()
    try:
        payload = decode_token(settings, token)
        user_id = payload.get("sub")
        token_type = payload.get("type")
        if not user_id or token_type != "access":
            return False, DEFAULT_MODE, ""
        mode = normalize_trading_mode(
            str(payload.get("trading_mode") or DEFAULT_MODE)
        )
        return True, mode, str(user_id)
    except JWTError:
        return False, DEFAULT_MODE, ""


async def _send_json(client: _Client, payload: dict) -> bool:
    """向单客户端发送 JSON；失败返回 False（调用方负责清理）"""
    try:
        await client.ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


async def _broadcast_to_user(user_id: str, payload: dict) -> None:
    """向指定用户在本进程的所有 WS 连接推送 JSON（订阅任务调用）"""
    if not user_id:
        return
    targets = [c for c in _clients if c.user_id == user_id]
    dead: list[_Client] = []
    for client in targets:
        if not await _send_json(client, payload):
            dead.append(client)
    for c in dead:
        if c in _clients:
            _clients.remove(c)


async def _publish_notify(user_id: str, payload: dict) -> None:
    """发布通知到 Redis 频道，由各 worker 的订阅任务投递到本地 _clients"""
    redis_conn = await _get_publish_redis()
    message = json.dumps({"user_id": user_id, "payload": payload}, ensure_ascii=False)
    await redis_conn.publish(_NOTIFY_CHANNEL, message)


def broadcast_notification(user_id: str, notification: dict) -> None:
    """异步推送通知到用户的 WS 连接（非阻塞，供 notify_svc 调用）

    多 worker 下通过 Redis pub/sub 跨进程广播：发布到 _NOTIFY_CHANNEL，
    各 worker 的订阅任务收到后投递给本地 _clients。
    """
    if not user_id:
        return
    payload = {"type": "notification", "data": notification}
    try:
        asyncio.create_task(_publish_notify(user_id, payload))
    except RuntimeError:
        # 无事件循环（如同步调用上下文），静默跳过
        pass


def broadcast_unread_count(user_id: str, unread: int) -> None:
    """异步推送未读数更新到用户的 WS 连接（跨 worker 经 Redis pub/sub）"""
    if not user_id:
        return
    payload = {"type": "unread_count", "data": {"unread": unread}}
    try:
        asyncio.create_task(_publish_notify(user_id, payload))
    except RuntimeError:
        pass


def broadcast_task_order(user_id: str, data: dict) -> None:
    """异步推送任务下单/平仓事件到用户的 WS 连接（跨 worker 经 Redis pub/sub）

    复用 ws:notify 单频道 + 按 user_id 定向分发；前端按 message.type ==
    "task_order_tick" 消费（AI 看盘行情页任务预警）。非阻塞，推送失败静默。
    """
    if not user_id:
        return
    payload = {"type": "task_order_tick", "data": data}
    try:
        asyncio.create_task(_publish_notify(user_id, payload))
    except RuntimeError:
        pass


def broadcast_anchor(user_id: str, data: dict) -> None:
    """异步推送 AI 看盘主播播报到用户的 WS 连接（跨 worker 经 Redis pub/sub）

    复用 ws:notify 单频道 + 按 user_id 定向分发；前端按 message.type ==
    "anchor_broadcast" 消费（行情页右栏 AI 主播 tab）。payload 自包含播报内容
    与 next_run_at，非阻塞，推送失败静默（落库不受影响）。
    """
    if not user_id:
        return
    payload = {"type": "anchor_broadcast", "data": data}
    try:
        asyncio.create_task(_publish_notify(user_id, payload))
    except RuntimeError:
        pass


async def _subscriber_loop(
    redis_conn: aioredis.Redis,
    channel: str,
    handler: Callable[[dict], Awaitable[None]],
    *,
    pattern: bool = False,
) -> None:
    retry_delay = _SUBSCRIBER_RETRY_INITIAL_SECONDS
    next_message_error_log_at = 0.0
    while True:
        pubsub = None
        try:
            pubsub = redis_conn.pubsub()
            subscribe = pubsub.psubscribe if pattern else pubsub.subscribe
            await asyncio.wait_for(
                subscribe(channel), timeout=_SUBSCRIBER_IO_TIMEOUT_SECONDS
            )
            logger.info("WS 订阅已连接 channel=%s", channel)
            while True:
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=_SUBSCRIBER_IO_TIMEOUT_SECONDS,
                )
                retry_delay = _SUBSCRIBER_RETRY_INITIAL_SECONDS
                if message is None:
                    continue
                try:
                    if not isinstance(message, dict):
                        raise ValueError("Invalid pubsub message")
                    await handler(message)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    now = time.monotonic()
                    if now >= next_message_error_log_at:
                        logger.warning(
                            "WS 订阅消息处理失败 channel=%s error_type=%s",
                            channel,
                            type(exc).__name__,
                        )
                        next_message_error_log_at = now + 30.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "WS 订阅连接失败 channel=%s error_type=%s retry_seconds=%.1f",
                channel,
                type(exc).__name__,
                retry_delay,
            )
        finally:
            if pubsub is not None:
                try:
                    close = getattr(pubsub, "aclose", None) or pubsub.close
                    await asyncio.wait_for(
                        close(), timeout=_SUBSCRIBER_CLOSE_TIMEOUT_SECONDS
                    )
                except Exception as exc:
                    logger.debug(
                        "WS 订阅连接清理失败 channel=%s error_type=%s",
                        channel,
                        type(exc).__name__,
                    )
        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, _SUBSCRIBER_RETRY_MAX_SECONDS)


async def _handle_notify_message(message: dict) -> None:
    raw = message.get("data")
    if not raw:
        return
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("Invalid notification envelope")
    user_id = envelope.get("user_id")
    payload = envelope.get("payload")
    if user_id and payload:
        if not isinstance(payload, dict):
            raise ValueError("Invalid notification payload")
        await _broadcast_to_user(str(user_id), payload)


async def _notify_subscriber_loop(redis_conn: aioredis.Redis) -> None:
    """订阅通知，按 user_id 分发；连接失败时退避并重建订阅。"""
    await _subscriber_loop(redis_conn, _NOTIFY_CHANNEL, _handle_notify_message)


async def start_notify_subscriber(redis_conn: aioredis.Redis) -> None:
    """启动本 worker 的通知订阅任务（lifespan 启动时调用）"""
    global _subscriber_task
    if _subscriber_task is None or _subscriber_task.done():
        _subscriber_task = asyncio.create_task(
            _notify_subscriber_loop(redis_conn), name="ws-notify-subscriber"
        )


async def _handle_big_order_message(message: dict) -> None:
    channel_raw = message.get("channel")
    channel_str = (
        channel_raw if isinstance(channel_raw, str)
        else (channel_raw.decode() if isinstance(channel_raw, bytes) else "")
    )
    mode = channel_str.rsplit(":", 1)[-1] if channel_str else ""
    raw = message.get("data")
    if not raw or not mode:
        return
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Invalid big order payload")
    targets = [c for c in _clients if c.trading_mode == mode]
    dead: list[_Client] = []
    for client in targets:
        if not await _send_json(client, payload):
            dead.append(client)
    for client in dead:
        if client in _clients:
            _clients.remove(client)


async def _big_order_subscriber_loop(redis_conn: aioredis.Redis) -> None:
    """订阅大单事件，按 trading_mode 分发；连接失败时退避重订阅。"""
    await _subscriber_loop(
        redis_conn,
        _BIG_ORDER_CHANNEL_PATTERN,
        _handle_big_order_message,
        pattern=True,
    )


async def start_big_order_subscriber(redis_conn: aioredis.Redis) -> None:
    """启动本 worker 的大单事件订阅任务（lifespan 启动时调用）"""
    global _big_order_subscriber_task
    if _big_order_subscriber_task is None or _big_order_subscriber_task.done():
        _big_order_subscriber_task = asyncio.create_task(
            _big_order_subscriber_loop(redis_conn), name="ws-big-order-subscriber"
        )


async def stop_big_order_subscriber() -> None:
    """停止大单事件订阅任务（lifespan 关闭时调用）"""
    global _big_order_subscriber_task
    if _big_order_subscriber_task is not None:
        _big_order_subscriber_task.cancel()
        try:
            await _big_order_subscriber_task
        except asyncio.CancelledError:
            pass
        _big_order_subscriber_task = None


async def _handle_ops_alert_message(message: dict) -> None:
    raw = message.get("data")
    if not raw:
        return
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Invalid ops alert payload")
    dead: list[_Client] = []
    for client in _clients:
        if not await _send_json(client, payload):
            dead.append(client)
    for client in dead:
        if client in _clients:
            _clients.remove(client)


async def _ops_alert_subscriber_loop(redis_conn: aioredis.Redis) -> None:
    """订阅运维告警并广播；连接失败时退避并重建订阅。"""
    await _subscriber_loop(redis_conn, _OPS_ALERT_CHANNEL, _handle_ops_alert_message)


async def start_ops_alert_subscriber(redis_conn: aioredis.Redis) -> None:
    """启动本 worker 的运维告警订阅任务（lifespan 启动时调用）"""
    global _ops_alert_subscriber_task
    if _ops_alert_subscriber_task is None or _ops_alert_subscriber_task.done():
        _ops_alert_subscriber_task = asyncio.create_task(
            _ops_alert_subscriber_loop(redis_conn), name="ws-ops-alert-subscriber"
        )


async def stop_ops_alert_subscriber() -> None:
    """停止运维告警订阅任务（lifespan 关闭时调用）"""
    global _ops_alert_subscriber_task
    if _ops_alert_subscriber_task is not None:
        _ops_alert_subscriber_task.cancel()
        try:
            await _ops_alert_subscriber_task
        except asyncio.CancelledError:
            pass
        _ops_alert_subscriber_task = None


async def stop_notify_subscriber() -> None:
    """停止通知订阅任务（lifespan 关闭时调用）"""
    global _subscriber_task, _publish_redis
    if _subscriber_task is not None:
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass
        _subscriber_task = None
    if _publish_redis is not None:
        try:
            await _publish_redis.close()
        except Exception:
            pass
        _publish_redis = None


async def _send_text(client: _Client, message: str) -> bool:
    try:
        await client.ws.send_text(message)
        return True
    except Exception:
        return False


async def _read_cached_by_pattern(redis_conn: Any, pattern: str) -> list[dict]:
    keys: list[str] = []
    async for key in redis_conn.scan_iter(pattern):
        keys.append(key)
    if not keys:
        return []
    raw_values = await redis_conn.mget(keys)
    results: list[dict] = []
    for raw in raw_values:
        if raw:
            results.append(json.loads(raw))
    return results


async def _read_cached_trades(redis_conn: Any, mode: str) -> dict[str, list[dict]]:
    pattern = trades_scan_pattern(mode)
    keys: list[str] = []
    async for key in redis_conn.scan_iter(pattern):
        keys.append(key)
    if not keys:
        return {}
    pipe = redis_conn.pipeline(transaction=False)
    for key in keys:
        pipe.lrange(key, 0, -1)
    raw_batches = await pipe.execute()
    trades_by_symbol: dict[str, list[dict]] = {}
    for key, raw_items in zip(keys, raw_batches):
        if not raw_items:
            continue
        key_str = key if isinstance(key, str) else key.decode()
        symbol = key_str.split(":")[-1]
        records = [json.loads(item) for item in raw_items]
        trades_by_symbol[symbol] = records
    return trades_by_symbol


async def _push_mode_snapshot(redis_conn: Any, client: _Client) -> None:
    """向单客户端推送其盘的缓存快照"""
    mode = client.trading_mode
    quotes, orderbooks, trades = await _collect_mode_data(redis_conn, mode)
    quotes = _stamp_and_filter_quotes(mode, quotes)
    if quotes:
        await _send_text(
            client,
            json.dumps({"type": "quote", "data": quotes}, ensure_ascii=False),
        )
    if orderbooks:
        await _send_text(
            client,
            json.dumps(
                {"type": "orderbook", "data": orderbooks}, ensure_ascii=False
            ),
        )
    if trades:
        await _send_text(
            client,
            json.dumps({"type": "trades", "data": trades}, ensure_ascii=False),
        )

    # K 线 forming：按盘分命名空间；尊重客户端订阅过滤
    from app.services.kline_keys import realtime_kline_scan_pattern

    klines = await _read_cached_by_pattern(
        redis_conn, realtime_kline_scan_pattern(mode)
    )
    klines = _filter_klines_for(client, klines)
    if klines:
        await _send_text(
            client,
            json.dumps({"type": "kline", "data": klines}, ensure_ascii=False),
        )


async def _read_cached_trades_legacy(redis_conn: Any) -> dict[str, list[dict]]:
    keys: list[str] = []
    async for key in redis_conn.scan_iter("market:trades:*"):
        # 排除 market:live:trades / market:virtual:trades
        key_str = key if isinstance(key, str) else key.decode()
        parts = key_str.split(":")
        if len(parts) == 3 and parts[0] == "market" and parts[1] == "trades":
            keys.append(key)
    trades_by_symbol: dict[str, list[dict]] = {}
    for key in keys:
        raw_items = await redis_conn.lrange(key, 0, -1)
        if not raw_items:
            continue
        key_str = key if isinstance(key, str) else key.decode()
        symbol = key_str.split(":")[-1]
        trades_by_symbol[symbol] = [json.loads(item) for item in raw_items]
    return trades_by_symbol


async def _collect_mode_data(
    redis_conn: Any, mode: str
) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
    """收集盘 quote/orderbook/trades；live 时 SimNow 最新日期>=sina 才用 SimNow 实时行情，否则整体回落 sina 防 stale 遮蔽"""
    if mode != "live":
        return (
            await _read_cached_by_pattern(redis_conn, quote_scan_pattern(mode)),
            await _read_cached_by_pattern(redis_conn, orderbook_scan_pattern(mode)),
            await _read_cached_trades(redis_conn, mode),
        )
    mode_q = await _read_cached_by_pattern(redis_conn, quote_scan_pattern("live"))
    legacy_q = await _read_cached_by_pattern(redis_conn, "market:quote:*")
    if max_trade_date(mode_q) >= max_trade_date(legacy_q):
        return (
            mode_q,
            await _read_cached_by_pattern(redis_conn, orderbook_scan_pattern("live")),
            await _read_cached_trades(redis_conn, "live"),
        )
    return (
        legacy_q,
        await _read_cached_by_pattern(redis_conn, "market:orderbook:*"),
        await _read_cached_trades_legacy(redis_conn),
    )


async def _broadcast_mode(redis_conn: Any, mode: str) -> None:
    """quote/orderbook/trades 全市场广播（入队发送，慢客户端不拖慢他人）"""
    targets = [c for c in _clients if c.trading_mode == mode]
    if not targets:
        return

    quotes, orderbooks, trades = await _collect_mode_data(redis_conn, mode)
    quotes = _stamp_and_filter_quotes(mode, quotes)
    if quotes:
        msg = json.dumps({"type": "quote", "data": quotes}, ensure_ascii=False)
        for c in targets:
            _offer(c, msg)
    if orderbooks:
        msg = json.dumps(
            {"type": "orderbook", "data": orderbooks}, ensure_ascii=False
        )
        for c in targets:
            _offer(c, msg)
    if trades:
        msg = json.dumps({"type": "trades", "data": trades}, ensure_ascii=False)
        for c in targets:
            _offer(c, msg)


def _broadcast_klines(mode: str, klines: list[dict]) -> None:
    """forming K 线按订阅分组广播：同订阅签名共享一份序列化结果。

    未订阅（None，兼容旧客户端）→ 全量；已订阅 → 只发订阅合约的 bar。
    """
    targets = [c for c in _clients if c.trading_mode == mode]
    if not targets or not klines:
        return
    groups: dict[frozenset[str] | None, list[_Client]] = {}
    for c in targets:
        groups.setdefault(c.kline_symbols, []).append(c)
    for sig, members in groups.items():
        if sig is None:
            payload = klines
        else:
            payload = [
                k for k in klines
                if str(k.get("symbol") or "").lower() in sig
            ]
        if not payload:
            continue
        msg = json.dumps({"type": "kline", "data": payload}, ensure_ascii=False)
        for c in members:
            _offer(c, msg)


def _advance_poll_deadline(previous: float, now: float, interval: float) -> float:
    candidate = previous + interval
    return candidate if candidate > now else now + interval


async def _poll_loop(redis_conn: Any) -> None:
    from app.services.kline_keys import realtime_kline_scan_pattern
    from app.services.refresh_cfg import get_refresh_interval
    from app.services.market_channel import read_channel_from_redis
    from app.core.trading_mode import MODE_LIVE

    loop = asyncio.get_running_loop()
    next_poll_at = loop.time() + _POLL_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(max(0.0, next_poll_at - loop.time()))
        try:
            modes = {c.trading_mode for c in _clients}
            for mode in modes:
                await _broadcast_mode(redis_conn, mode)
                # forming K 线按盘推送，禁止 live/virtual 串；按订阅过滤
                targets = [c for c in _clients if c.trading_mode == mode]
                if not targets:
                    continue
                klines = await _read_cached_by_pattern(
                    redis_conn, realtime_kline_scan_pattern(mode)
                )
                _broadcast_klines(mode, klines)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("行情轮询广播异常", exc_info=True)
        channel = await read_channel_from_redis(redis_conn, MODE_LIVE)
        poll_interval = await get_refresh_interval(redis_conn, channel)
        now = loop.time()
        next_poll_at = _advance_poll_deadline(next_poll_at, now, poll_interval)


@router.websocket("/ws/market")
async def websocket_market(websocket: WebSocket) -> None:
    """WebSocket /ws/market —— 按 token 内 trading_mode 推送对应盘行情"""
    global _poll_task

    token = websocket.query_params.get("token")
    ok, mode, user_id = _parse_ws_auth(token)
    if not ok:
        await websocket.accept()
        await websocket.close(code=_WS_CLOSE_UNAUTHORIZED, reason="Unauthorized")
        logger.warning("WebSocket 鉴权失败，已拒绝连接")
        return

    await websocket.accept()
    client = _Client(ws=websocket, trading_mode=mode, user_id=user_id)
    _clients.append(client)
    _start_sender(client)
    logger.info(
        "WebSocket 已连接 mode=%s 当前连接数=%d", mode, len(_clients)
    )

    redis_conn = websocket.app.state.redis
    await _push_mode_snapshot(redis_conn, client)

    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(_poll_loop(redis_conn))
        logger.info("已启动行情轮询任务，间隔 %d 秒", _POLL_INTERVAL_SECONDS)

    try:
        while True:
            msg = await websocket.receive_text()
            _handle_client_message(client, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("WebSocket 连接异常", exc_info=True)
    finally:
        if client in _clients:
            _clients.remove(client)
        if client.sender is not None:
            client.sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await client.sender
            client.sender = None
        logger.info(
            "WebSocket 已断开 mode=%s 当前连接数=%d", mode, len(_clients)
        )
        if not _clients and _poll_task is not None:
            _poll_task.cancel()
            _poll_task = None


def _handle_client_message(client: _Client, msg: str) -> None:
    """客户端上行消息处理：心跳回 pong；JSON 订阅指令更新 K 线过滤集。

    协议：{"action":"subscribe_kline","symbols":["rb2610",...]} 订阅，
    {"action":"unsubscribe_kline"} 恢复全量。未知消息忽略（向后兼容）。
    """
    text = str(msg or "").strip()
    lowered = text.lower()
    if not text or lowered in ("ping", "pong"):
        if lowered == "ping":
            # 回 pong 供客户端判活：仅靠推送方向检测半开连接会漏判
            # （server→client 静默死亡时客户端 readyState 仍 OPEN）。
            # 纯文本经 sender 队列发出，兼容旧客户端（其 handleMessage 忽略）。
            _offer(client, "pong")
        return
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    action = str(data.get("action") or "").strip().lower()
    if action == "subscribe_kline":
        raw = data.get("symbols")
        symbols = {
            str(s or "").strip().lower()
            for s in (raw if isinstance(raw, list) else [])
            if str(s or "").strip()
        }
        client.kline_symbols = frozenset(symbols) if symbols else None
    elif action == "unsubscribe_kline":
        client.kline_symbols = None


# ---------------- 对外展示站 WS（API key 认证 + 轻量摘要推送） ----------------

_EXT_POLL_INTERVAL = 1  # 聚合轮询步长（秒）
_EXT_DEFAULT_INTERVAL = 5
_EXT_MIN_INTERVAL = 1
_EXT_MAX_INTERVAL = 30


@dataclass
class _ExtClient:
    ws: WebSocket
    interval: int  # 推送间隔秒（已 clamp 到 [1,30]）
    last_push_at: float = 0.0  # 单调时钟，节流用


_external_clients: list[_ExtClient] = []
_ext_poll_task: asyncio.Task | None = None


async def _ext_send_json(client: _ExtClient, payload: dict) -> bool:
    try:
        await client.ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


async def _ext_send_text(client: _ExtClient, msg: str) -> bool:
    """发送预序列化文本（批量推送时复用同一份 JSON 字符串）"""
    try:
        await client.ws.send_text(msg)
        return True
    except Exception:
        return False


async def _ext_poll_loop(redis_conn: Any, session_factory: Any) -> None:
    """每 1s 聚合一次轻量 showcase（无 equity），按各 client 的 interval 节流推送。"""
    from app.services.ai_trading.task import showcase_svc

    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(_EXT_POLL_INTERVAL)
        try:
            clients = list(_external_clients)
            if not clients:
                continue
            async with session_factory() as session:
                payload = await showcase_svc.list_showcase(
                    session, redis_conn, 12, None, include_equity=False
                )
            now = loop.time()
            due = [c for c in clients if now - c.last_push_at >= c.interval]
            if due:
                # 预序列化一次，所有 client 共享同一份 JSON；并发发送避免慢 client 阻塞
                msg = json.dumps(payload, ensure_ascii=False)
                results = await asyncio.gather(
                    *[_ext_send_text(c, msg) for c in due]
                )
                for client, ok in zip(due, results):
                    if ok:
                        client.last_push_at = now
                    elif client in _external_clients:
                        _external_clients.remove(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("对外展示 WS 推送异常", exc_info=True)


@router.websocket("/ws/public/ai-trading")
async def websocket_public_ai_trading(websocket: WebSocket) -> None:
    """对外展示站 WS：?key=<api_key>&interval=<1-30>

    API key 认证（query）；推送轻量 showcase 摘要（无 equity_series）。
    收益曲线由展示站走 HTTP /api/public/ai-trading 拉。
    """
    global _ext_poll_task

    raw_key = (websocket.query_params.get("key") or "").strip()
    try:
        interval = int(
            websocket.query_params.get("interval") or _EXT_DEFAULT_INTERVAL
        )
    except (TypeError, ValueError):
        interval = _EXT_DEFAULT_INTERVAL
    interval = max(_EXT_MIN_INTERVAL, min(_EXT_MAX_INTERVAL, interval))

    session_factory = websocket.app.state.session_factory
    redis_conn = websocket.app.state.redis

    record = None
    try:
        from app.services.api_key_svc import authenticate

        async with session_factory() as session:
            record = await authenticate(session, raw_key)
            await session.commit()
    except Exception:
        logger.warning("对外 WS 鉴权异常", exc_info=True)
        record = None

    if record is None:
        await websocket.accept()
        await websocket.close(
            code=_WS_CLOSE_UNAUTHORIZED, reason="Invalid API Key"
        )
        logger.warning("对外 WS 鉴权失败，已拒绝连接")
        return

    await websocket.accept()
    client = _ExtClient(ws=websocket, interval=interval)
    _external_clients.append(client)
    logger.info(
        "对外展示 WS 已连接 interval=%ds 当前连接数=%d",
        interval,
        len(_external_clients),
    )

    # 推一次首帧（立即可见，不必等第一个 interval）
    try:
        from app.services.ai_trading.task import showcase_svc as _sc

        async with session_factory() as session:
            payload = await _sc.list_showcase(
                session, redis_conn, 12, None, include_equity=False
            )
        if await _ext_send_json(client, payload):
            client.last_push_at = asyncio.get_running_loop().time()
    except Exception:
        logger.warning("对外 WS 首帧推送失败", exc_info=True)

    if _ext_poll_task is None or _ext_poll_task.done():
        _ext_poll_task = asyncio.create_task(
            _ext_poll_loop(redis_conn, session_factory)
        )
        logger.info("已启动对外展示 WS 推送任务，步长 %ds", _EXT_POLL_INTERVAL)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("对外展示 WS 连接异常", exc_info=True)
    finally:
        if client in _external_clients:
            _external_clients.remove(client)
        logger.info(
            "对外展示 WS 已断开 当前连接数=%d", len(_external_clients)
        )
        if not _external_clients and _ext_poll_task is not None:
            _ext_poll_task.cancel()
            _ext_poll_task = None
