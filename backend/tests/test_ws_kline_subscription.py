"""WS K 线订阅过滤与客户端消息解析（广播层纯逻辑）"""

from __future__ import annotations

import json

from app.api import ws as ws_api
from app.api.ws import _Client, _broadcast_klines, _handle_client_message


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self) -> None:
        self.closed = True


def _make_client(mode="live", symbols=None) -> _Client:
    fake_ws = _FakeWebSocket()
    # dataclass 的 queue/sender 字段由构造器默认生成，这里仅用广播路径
    client = _Client(ws=fake_ws, trading_mode=mode, kline_symbols=symbols)
    client._fake = fake_ws  # type: ignore[attr-defined]
    return client


def _klines() -> list[dict]:
    return [
        {"symbol": "rb2610", "period": "1m", "bar": {"time": "t1"}},
        {"symbol": "hc2610", "period": "1m", "bar": {"time": "t2"}},
        {"symbol": "rb2610", "period": "5m", "bar": {"time": "t3"}},
    ]


def _drain(client: _Client) -> list[dict]:
    """取出客户端发送队列中的全部消息（广播入队，sender 任务负责发送）"""
    out = []
    while not client.queue.empty():
        out.append(json.loads(client.queue.get_nowait()))
    return out


def test_broadcast_full_stream_without_subscription(monkeypatch):
    """未订阅（兼容旧客户端）→ 全量 forming bar"""
    full = _make_client()
    subscribed = _make_client(symbols=frozenset({"rb2610"}))
    monkeypatch.setattr(ws_api, "_clients", [full, subscribed])

    _broadcast_klines("live", _klines())

    msgs = _drain(full)
    assert len(msgs) == 1
    assert msgs[0]["type"] == "kline"
    assert len(msgs[0]["data"]) == 3  # 全量


def test_broadcast_filters_by_subscription(monkeypatch):
    subscribed = _make_client(symbols=frozenset({"rb2610"}))
    monkeypatch.setattr(ws_api, "_clients", [subscribed])

    _broadcast_klines("live", _klines())

    msgs = _drain(subscribed)
    assert [k["symbol"] for k in msgs[0]["data"]] == ["rb2610", "rb2610"]


def test_broadcast_groups_share_serialization(monkeypatch):
    """同订阅签名共享一份序列化结果；跨盘不串"""
    a = _make_client(symbols=frozenset({"rb2610"}))
    b = _make_client(symbols=frozenset({"rb2610"}))
    other_mode = _make_client(mode="virtual", symbols=frozenset({"rb2610"}))
    monkeypatch.setattr(ws_api, "_clients", [a, b, other_mode])

    _broadcast_klines("live", _klines())

    msgs_a, msgs_b, msgs_other = _drain(a), _drain(b), _drain(other_mode)
    assert len(msgs_a) == 1 and len(msgs_b) == 1
    assert msgs_a == msgs_b  # 同签名共享同一份序列化结果
    assert msgs_other == []  # virtual 盘不收 live 广播


def test_handle_client_message_updates_subscription():
    client = _make_client()

    _handle_client_message(
        client, json.dumps({"action": "subscribe_kline", "symbols": ["RB2610", " "]})
    )
    assert client.kline_symbols == frozenset({"rb2610"})  # 归一小写、去空

    _handle_client_message(client, json.dumps({"action": "subscribe_kline", "symbols": []}))
    assert client.kline_symbols is None  # 空订阅 → 恢复全量

    _handle_client_message(
        client, json.dumps({"action": "subscribe_kline", "symbols": ["hc2610"]})
    )
    _handle_client_message(client, json.dumps({"action": "unsubscribe_kline"}))
    assert client.kline_symbols is None


def test_handle_client_message_ignores_ping_and_garbage():
    client = _make_client(symbols=frozenset({"rb2610"}))
    _handle_client_message(client, "ping")
    _handle_client_message(client, "not-json")
    _handle_client_message(client, json.dumps({"action": "unknown"}))
    assert client.kline_symbols == frozenset({"rb2610"})


def test_handle_client_message_ping_replies_pong():
    """纯文本 ping → 入队 pong（经 sender 发送，供客户端判活）；
    pong/空文本/非 JSON 不产生任何消息"""
    client = _make_client()
    _handle_client_message(client, "ping")
    assert client.queue.get_nowait() == "pong"

    _handle_client_message(client, "pong")
    _handle_client_message(client, "")
    _handle_client_message(client, "not-json")
    assert client.queue.empty()
