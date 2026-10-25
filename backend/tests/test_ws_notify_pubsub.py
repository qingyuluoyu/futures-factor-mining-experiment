"""WS 通知跨 worker 广播（Redis pub/sub）单元测试

根因：uvicorn --workers N 下 _clients 是进程内存，broadcast_* 只能投递本进程连接，
通知产生 worker 与用户 WS 连接 worker 不同时推送丢失。
修复：broadcast_* 改为 publish 到 Redis 频道，每个 worker 的订阅任务投递到本地 _clients。

本测试覆盖「发布」与「订阅投递」两半，组合即等价跨 worker 广播。
风格沿用 test_ws_poll_cadence.py：FakeRedis + 直接调 ws._xxx。

重要：绝不让被测代码连真实 Redis（CI 容器隔离网络连不上会卡死）。
通过 monkeypatch 把 ws._publish_notify 替换为可控假实现。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.api import ws

# 在任何 fixture/monkeypatch 之前抓取真实 _publish_notify 引用，
# 供 test_publish_notify_serializes_envelope_to_channel 临时还原后直测其序列化逻辑。
_REAL_PUBLISH_NOTIFY = ws._publish_notify


@pytest.fixture(autouse=True)
def _isolate_ws(monkeypatch):
    """每个用例隔离 ws 模块全局 + 拦截真实 Redis 连接

    - 清空 _clients / _publish_redis，避免用例间污染
    - 用假 _publish_notify 替换真实发布（默认收集调用），防止连真实 Redis
    """

    async def _fake_publish(user_id: str, payload: dict) -> None:
        _isolate_ws.calls.append({"user_id": user_id, "payload": payload})

    _isolate_ws.calls = []

    saved_clients = list(ws._clients)
    saved_pub = ws._publish_redis
    saved_publish_fn = ws._publish_notify
    ws._clients.clear()
    ws._publish_redis = None
    monkeypatch.setattr(ws, "_publish_notify", _fake_publish)

    yield

    ws._clients.clear()
    ws._clients.extend(saved_clients)
    ws._publish_redis = saved_pub
    monkeypatch.setattr(ws, "_publish_notify", saved_publish_fn)


_isolate_ws.calls: list = []  # type: ignore[misc]


async def _drain() -> None:
    """让事件循环把 broadcast_* create_task 出的 _publish_notify 跑完"""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_broadcast_notification_publishes_envelope() -> None:
    """broadcast_notification 应发布含 user_id + payload 的消息到发布函数"""
    notif = {"id": "n1", "title": "订单成交", "category": "trade"}
    ws.broadcast_notification("user-abc", notif)
    await _drain()

    assert len(_isolate_ws.calls) == 1
    call = _isolate_ws.calls[0]
    assert call["user_id"] == "user-abc"
    assert call["payload"] == {"type": "notification", "data": notif}


@pytest.mark.asyncio
async def test_broadcast_unread_count_publishes_envelope() -> None:
    """broadcast_unread_count 同样走发布函数"""
    ws.broadcast_unread_count("user-xyz", 7)
    await _drain()

    assert len(_isolate_ws.calls) == 1
    call = _isolate_ws.calls[0]
    assert call["user_id"] == "user-xyz"
    assert call["payload"] == {"type": "unread_count", "data": {"unread": 7}}


@pytest.mark.asyncio
async def test_broadcast_skips_empty_user_id() -> None:
    """空 user_id 直接返回，不发布"""
    ws.broadcast_notification("", {"id": "n1"})
    await _drain()
    assert _isolate_ws.calls == []


@pytest.mark.asyncio
async def test_publish_notify_serializes_envelope_to_channel(monkeypatch):
    """真实 _publish_notify 把 user_id+payload 序列化为 JSON 发布到 _NOTIFY_CHANNEL

    直接调用真实实现（绕过 fixture 默认替换的假 _publish_notify），
    注入假 Redis，不连真实 Redis。
    """
    import app.api.ws as ws_mod

    # fixture 已把模块的 _publish_notify 换成假实现；这里临时换回真实实现
    real_publish = _REAL_PUBLISH_NOTIFY
    monkeypatch.setattr(ws_mod, "_publish_notify", real_publish)

    captured: list[tuple[str, str]] = []

    class _CaptureRedis:
        async def publish(self, channel: str, message: str) -> int:
            captured.append((channel, message))
            return 1

    monkeypatch.setattr(ws_mod, "_publish_redis", _CaptureRedis())

    await ws_mod._publish_notify(
        "u-serial", {"type": "notification", "data": {"id": "x"}}
    )

    assert len(captured) == 1
    channel, message = captured[0]
    assert channel == ws_mod._NOTIFY_CHANNEL
    envelope = json.loads(message)
    assert envelope == {
        "user_id": "u-serial",
        "payload": {"type": "notification", "data": {"id": "x"}},
    }


@pytest.mark.asyncio
async def test_broadcast_to_user_delivers_only_to_matching_connections() -> None:
    """_broadcast_to_user 只投递给本进程 user_id 匹配的连接（订阅任务分发逻辑）"""
    target_ws = AsyncMock()
    other_ws = AsyncMock()
    ws._clients.append(ws._Client(ws=target_ws, trading_mode="live", user_id="u1"))
    ws._clients.append(ws._Client(ws=other_ws, trading_mode="live", user_id="u2"))

    payload = {"type": "notification", "data": {"id": "n1"}}
    await ws._broadcast_to_user("u1", payload)

    target_ws.send_text.assert_awaited_once()
    other_ws.send_text.assert_not_called()
    sent = target_ws.send_text.call_args.args[0]
    assert json.loads(sent) == payload


@pytest.mark.asyncio
async def test_broadcast_to_user_removes_dead_connection() -> None:
    """投递失败的连接应从 _clients 清理"""
    dead_ws = AsyncMock()
    dead_ws.send_text = AsyncMock(side_effect=Exception("连接已断开"))
    client = ws._Client(ws=dead_ws, trading_mode="live", user_id="u1")
    ws._clients.append(client)

    await ws._broadcast_to_user("u1", {"type": "notification", "data": {}})

    assert client not in ws._clients


class _FakePubSub:
    """模拟 redis pubsub：按预设消息序列返回，耗尽后阻塞（靠 cancel 终止循环）"""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self._empty_event = asyncio.Event()
        # 立即 set：耗尽后 get_message 立即返回 None（循环 continue），
        # 由测试在处理完消息后 cancel task 来终止循环。
        self._empty_event.set()

    async def subscribe(self, *channels: str) -> None:
        self.subscribed.extend(channels)

    async def get_message(
        self, *, ignore_subscribe_messages: bool = True, timeout: float = 1.0
    ) -> dict | None:
        if self._messages:
            return self._messages.pop(0)
        # 耗尽后短暂让出控制权再返回 None，让 cancel 有机会插入
        await asyncio.sleep(0)
        return None

    async def unsubscribe(self, *channels: str) -> None:
        pass

    async def close(self) -> None:
        pass


class _FakeSubscribeRedis:
    def __init__(self, messages: list[dict]) -> None:
        self._pubsub = _FakePubSub(messages)

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


@pytest.mark.asyncio
async def test_subscriber_loop_delivers_published_envelope() -> None:
    """订阅循环收到频道消息后，按 user_id 投递给本地匹配连接（跨 worker 广播的订阅半）"""
    target_ws = AsyncMock()
    ws._clients.append(ws._Client(ws=target_ws, trading_mode="live", user_id="u1"))

    envelope = {
        "user_id": "u1",
        "payload": {"type": "notification", "data": {"id": "n9", "title": "T"}},
    }
    fake_redis = _FakeSubscribeRedis([{"data": json.dumps(envelope)}])

    # 后台跑订阅循环；处理完消息后 cancel 终止（_publish_notify 已隔离，不会卡）
    task = asyncio.create_task(ws._notify_subscriber_loop(fake_redis))  # type: ignore[arg-type]
    await _drain()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ws._NOTIFY_CHANNEL in fake_redis._pubsub.subscribed
    target_ws.send_text.assert_awaited_once()
    sent = target_ws.send_text.call_args.args[0]
    assert json.loads(sent) == envelope["payload"]
