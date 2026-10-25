from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError

from app.api import ws


_SUBSCRIBERS = [
    (ws._notify_subscriber_loop, "ws:notify", False),
    (ws._big_order_subscriber_loop, "ws:big-order:*", True),
    (ws._ops_alert_subscriber_loop, "ws:ops-alert", False),
]


class _PubSub:
    def __init__(self, messages=(), *, subscribe_error=None, close_error=None):
        self.messages = list(messages)
        self.subscribe_error = subscribe_error
        self.close_error = close_error
        self.subscriptions = []
        self.closed = False
        self.reads = 0
        self.waiting = asyncio.Event()

    async def subscribe(self, channel):
        self.subscriptions.append((channel, False))
        if self.subscribe_error:
            raise self.subscribe_error

    async def psubscribe(self, channel):
        self.subscriptions.append((channel, True))
        if self.subscribe_error:
            raise self.subscribe_error

    async def get_message(self, **kwargs):
        self.reads += 1
        await asyncio.sleep(0)
        if self.messages:
            message = self.messages.pop(0)
            if isinstance(message, BaseException):
                raise message
            return message
        self.waiting.set()
        await asyncio.Event().wait()

    async def unsubscribe(self, *channels):
        pass

    async def punsubscribe(self, *channels):
        pass

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class _Redis:
    def __init__(self, connections):
        self.connections = list(connections)
        self.created = []

    def pubsub(self):
        connection = self.connections.pop(0)
        self.created.append(connection)
        return connection


@pytest.fixture(autouse=True)
def _isolate_clients(monkeypatch):
    monkeypatch.setattr(ws, "_clients", [])


def _message(channel):
    payload = {"type": "event", "data": {"id": "recovered"}}
    body = {"user_id": "target", "payload": payload} if channel == "ws:notify" else payload
    return {"channel": b"ws:big-order:live", "data": json.dumps(body)}, payload


async def _cancel(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
@pytest.mark.parametrize("failure_stage", ["subscribe", "read"])
async def test_subscriber_recreates_connection_and_recovers_routing(
    monkeypatch, caplog, subscriber, channel, pattern, failure_stage
):
    message, payload = _message(channel)
    failure = ConnectionError("redis://secret-password@private-host:6379")
    broken = _PubSub(
        [failure] if failure_stage == "read" else [],
        subscribe_error=failure if failure_stage == "subscribe" else None,
        close_error=ConnectionError("cleanup secret-password"),
    )
    recovered = _PubSub([message])
    redis_conn = _Redis([broken, recovered])
    target = AsyncMock()
    other_user = AsyncMock()
    other_mode = AsyncMock()
    ws._clients.extend([
        ws._Client(ws=target, trading_mode="live", user_id="target"),
        ws._Client(ws=other_user, trading_mode="live", user_id="other"),
        ws._Client(ws=other_mode, trading_mode="virtual", user_id="target"),
    ])
    delays = []
    real_sleep = asyncio.sleep

    async def sleep(delay):
        delays.append(delay)
        assert broken.closed
        await real_sleep(0)

    monkeypatch.setattr(ws, "asyncio", SimpleNamespace(
        sleep=sleep, CancelledError=asyncio.CancelledError, wait_for=asyncio.wait_for
    ))
    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        task = asyncio.create_task(subscriber(redis_conn))
        try:
            await asyncio.wait_for(recovered.waiting.wait(), timeout=0.2)
        finally:
            if not task.done():
                await _cancel(task)

    assert delays == [1.0]
    assert redis_conn.created == [broken, recovered]
    assert recovered.subscriptions == [(channel, pattern)]
    assert broken.reads <= 1
    assert recovered.closed
    assert json.loads(target.send_text.call_args.args[0]) == payload
    assert other_user.send_text.await_count == (0 if channel == "ws:notify" else 1)
    assert other_mode.send_text.await_count == (0 if pattern else 1)
    assert "secret-password" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
async def test_subscriber_backoff_is_capped_and_cancellation_closes_connection(
    monkeypatch, subscriber, channel, pattern
):
    connections = [_PubSub([ConnectionError("offline")]) for _ in range(8)]
    redis_conn = _Redis(connections)
    delays = []

    async def sleep(delay):
        delays.append(delay)
        assert redis_conn.created[-1].closed
        if len(delays) == 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(ws, "asyncio", SimpleNamespace(
        sleep=sleep, CancelledError=asyncio.CancelledError, wait_for=asyncio.wait_for
    ))
    task = asyncio.create_task(subscriber(redis_conn))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    assert all(connection.closed for connection in connections)
    assert all(connection.reads == 1 for connection in connections)


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
async def test_malformed_messages_do_not_reconnect_and_next_message_is_delivered(
    caplog, subscriber, channel, pattern
):
    message, payload = _message(channel)
    connection = _PubSub([
        {**message, "data": "{private-upload-content"},
        {**message, "data": "[]"},
        message,
    ])
    redis_conn = _Redis([connection])
    client_ws = AsyncMock()
    ws._clients.append(ws._Client(ws=client_ws, trading_mode="live", user_id="target"))
    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        task = asyncio.create_task(subscriber(redis_conn))
        try:
            await asyncio.wait_for(connection.waiting.wait(), timeout=0.2)
        finally:
            if not task.done():
                await _cancel(task)
    assert redis_conn.created == [connection]
    assert connection.closed
    client_ws.send_text.assert_awaited_once()
    assert json.loads(client_ws.send_text.call_args.args[0]) == payload
    assert "private-upload-content" not in caplog.text
    assert len(caplog.records) == 1
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
async def test_cancellation_during_initial_subscription_closes_pubsub(
    subscriber, channel, pattern
):
    entered = asyncio.Event()

    class _BlockedPubSub(_PubSub):
        async def subscribe(self, channel):
            entered.set()
            await asyncio.Event().wait()

        psubscribe = subscribe

    connection = _BlockedPubSub()
    task = asyncio.create_task(subscriber(_Redis([connection])))
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    await _cancel(task)
    assert connection.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
async def test_cancellation_during_backoff_does_not_open_another_connection(
    monkeypatch, subscriber, channel, pattern
):
    entered = asyncio.Event()
    connection = _PubSub([ConnectionError("offline")])
    redis_conn = _Redis([connection])

    async def sleep(delay):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ws, "asyncio", SimpleNamespace(
        sleep=sleep, CancelledError=asyncio.CancelledError, wait_for=asyncio.wait_for
    ))
    task = asyncio.create_task(subscriber(redis_conn))
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    await _cancel(task)
    assert connection.closed
    assert redis_conn.created == [connection]


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
async def test_cancellation_bounds_a_stalled_connection_close(
    monkeypatch, subscriber, channel, pattern
):
    monkeypatch.setattr(ws, "_SUBSCRIBER_CLOSE_TIMEOUT_SECONDS", 0.01, raising=False)

    class _StalledClosePubSub(_PubSub):
        async def close(self):
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True

    connection = _StalledClosePubSub()
    task = asyncio.create_task(subscriber(_Redis([connection])))
    await asyncio.wait_for(connection.waiting.wait(), timeout=0.2)
    await _cancel(task)
    assert connection.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("subscriber,channel,pattern", _SUBSCRIBERS)
async def test_valid_message_resets_failure_backoff(
    monkeypatch, subscriber, channel, pattern
):
    message, payload = _message(channel)
    connections = [
        _PubSub([ConnectionError("offline")]),
        _PubSub([ConnectionError("offline")]),
        _PubSub([message, ConnectionError("offline again")]),
    ]
    redis_conn = _Redis(connections)
    client_ws = AsyncMock()
    ws._clients.append(ws._Client(ws=client_ws, trading_mode="live", user_id="target"))
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(ws, "asyncio", SimpleNamespace(
        sleep=sleep, CancelledError=asyncio.CancelledError, wait_for=asyncio.wait_for
    ))
    task = asyncio.create_task(subscriber(redis_conn))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.2)
    assert delays == [1.0, 2.0, 1.0]
    client_ws.send_text.assert_awaited_once()
    assert json.loads(client_ws.send_text.call_args.args[0]) == payload
    assert all(connection.closed for connection in connections)
