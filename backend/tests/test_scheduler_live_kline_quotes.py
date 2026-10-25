"""scheduler live K 线 forming 行情来源测试（real_ctp → CTP 源键；其余 → sina 源键）"""

from __future__ import annotations

import json
import re
import time

import pytest

from app.services import scheduler


class _FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(store or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def scan_iter(self, pattern: str):
        regex = re.escape(pattern).replace(r"\*", ".*")
        for key in list(self.store):
            if re.fullmatch(regex, key):
                yield key


def _quote(
    symbol: str,
    last_price: float,
    recv_ts: float | None = None,
) -> str:
    payload = {
        "symbol": symbol,
        "last_price": last_price,
        "volume": 100,
        "position": 50,
        "open_price": last_price - 1,
        "high_price": last_price + 2,
        "low_price": last_price - 2,
        "avg_price": last_price,
    }
    if recv_ts is not None:
        payload["recv_ts"] = recv_ts
    return json.dumps(payload)


@pytest.mark.asyncio
async def test_live_kline_quotes_uses_real_ctp_sources(monkeypatch) -> None:
    """real_ctp 渠道：从 bridge 源键收集，过滤无效值"""
    store = {
        "market:live:source:real_ctp:rb2610": _quote("rb2610", 3010.0),
        "market:live:source:real_ctp:hc2610": _quote("hc2610", 3110.0),
        # last_price=0 应被过滤
        "market:live:source:real_ctp:zz2609": json.dumps(
            {"symbol": "zz2609", "last_price": 0}
        ),
    }
    r = _FakeRedis(store)

    async def _channel(redis_conn, mode: str) -> str:
        return "real_ctp"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    quotes = await scheduler._live_kline_quotes(r)
    assert {q["symbol"] for q in quotes} == {"rb2610", "hc2610"}
    assert quotes[0]["last_price"] in (3010.0, 3110.0)


@pytest.mark.asyncio
async def test_live_kline_quotes_falls_back_to_sina_source_keys(monkeypatch) -> None:
    """非 real_ctp 渠道：读 sina-bridge 写的 sina 源键（无直连）"""
    store = {
        "market:live:source:sina:rb2610": _quote("rb2610", 3010.0),
        "market:live:source:sina:hc2610": _quote("hc2610", 3110.0),
        # last_price 缺失应被过滤
        "market:live:source:sina:zz2609": json.dumps({"symbol": "zz2609"}),
    }
    r = _FakeRedis(store)

    async def _channel(redis_conn, mode: str) -> str:
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    quotes = await scheduler._live_kline_quotes(r)
    assert {q["symbol"] for q in quotes} == {"rb2610", "hc2610"}


@pytest.mark.asyncio
async def test_live_kline_quotes_sina_no_data(monkeypatch) -> None:
    """sina 渠道但源键为空（桥未启动/未供数）：返回空（调用方跳过更新）"""
    r = _FakeRedis()

    async def _channel(redis_conn, mode: str) -> str:
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    assert await scheduler._live_kline_quotes(r) == []


@pytest.mark.asyncio
async def test_live_kline_quotes_real_ctp_no_data(monkeypatch) -> None:
    """real_ctp 渠道但 Redis 无数据：返回空（调用方跳过更新）"""
    r = _FakeRedis()

    async def _channel(redis_conn, mode: str) -> str:
        return "real_ctp"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    assert await scheduler._live_kline_quotes(r) == []


@pytest.mark.asyncio
async def test_live_kline_quotes_filters_stale_recv_ts(monkeypatch) -> None:
    """源键 recv_ts 超窗过滤：桥停推后源键靠 TTL(7d) 残留，陈旧 quote
    不得再以昨夜价格喂当前桶 forming（2026-08-26 实测 5 个非主力陈旧 10.5h）"""
    now = time.time()
    store = {
        "market:live:source:sina:ma2701": _quote("ma2701", 2600.0, recv_ts=now - 10),
        "market:live:source:sina:rb2701": _quote("rb2701", 3100.0, recv_ts=now - 30),
        # 陈旧 2 小时：应被过滤
        "market:live:source:sina:wh2707": _quote("wh2707", 2500.0, recv_ts=now - 7200),
        # 陈旧 10.5 小时（实测场景）：应被过滤
        "market:live:source:sina:if2612": _quote("if2612", 3900.0, recv_ts=now - 37800),
    }
    r = _FakeRedis(store)

    async def _channel(redis_conn, mode: str) -> str:
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    quotes = await scheduler._live_kline_quotes(r)
    assert {q["symbol"] for q in quotes} == {"ma2701", "rb2701"}


@pytest.mark.asyncio
async def test_live_kline_quotes_missing_recv_ts_passes(monkeypatch) -> None:
    """recv_ts 缺失（老格式源键）放行：避免误杀导致 forming 全停，
    最坏退回无过滤行为"""
    store = {
        "market:live:source:sina:ma2701": _quote("ma2701", 2600.0),
        "market:live:source:sina:rb2701": _quote("rb2701", 3100.0),
    }
    r = _FakeRedis(store)

    async def _channel(redis_conn, mode: str) -> str:
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    quotes = await scheduler._live_kline_quotes(r)
    assert {q["symbol"] for q in quotes} == {"ma2701", "rb2701"}


@pytest.mark.asyncio
async def test_live_kline_quotes_stale_boundary(monkeypatch) -> None:
    """recv_ts 恰好在窗口内（90s）放行；略超窗口过滤"""
    now = time.time()
    store = {
        "market:live:source:sina:ma2701": _quote("ma2701", 2600.0, recv_ts=now - 89),
        "market:live:source:sina:rb2701": _quote("rb2701", 3100.0, recv_ts=now - 91),
    }
    r = _FakeRedis(store)

    async def _channel(redis_conn, mode: str) -> str:
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    quotes = await scheduler._live_kline_quotes(r)
    assert {q["symbol"] for q in quotes} == {"ma2701"}
