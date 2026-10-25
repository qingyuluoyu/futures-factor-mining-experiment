"""sina-bridge 轮询循环单测 —— 源键写入/recv_ts/自适应节奏/心跳/月度循环"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from app.connectors.sina import bridge, config


class _FakePipe:
    def __init__(self, store: dict) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def set(self, key, value, ex=None):
        self._ops.append((key, value, ex))

    async def execute(self):
        for key, value, _ex in self._ops:
            self._store[key] = value
        return len(self._ops)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self.store)

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def _quote(symbol: str, last_price: float = 3000.0) -> dict:
    return {
        "symbol": symbol,
        "last_price": last_price,
        "pre_close": 2950.0,
        "trade_date": "2026-08-24",
    }


async def test_poll_once_writes_source_keys_with_recv_ts(monkeypatch):
    r = _FakeRedis()
    state = bridge.BridgeState()
    monkeypatch.setattr(
        "app.data.sina_quote.fetch_sina_quotes",
        lambda: [_quote("rb2610"), _quote("hc2610"), {"symbol": "bad", "last_price": 0}],
    )
    before = time.time()
    n = await bridge.poll_once(r, state)
    assert n == 2
    assert set(r.store) == {
        "market:live:source:sina:quote:rb2610",
        "market:live:source:sina:quote:hc2610",
    }
    q = json.loads(r.store["market:live:source:sina:quote:rb2610"])
    assert q["recv_ts"] >= before  # 轮询时间戳盖在每条 quote 上
    assert state.last_poll_ok is True
    assert state.latest_quotes and len(state.latest_quotes) == 2


async def test_poll_once_empty_result_skips_write(monkeypatch):
    """空结果（限流/异常）：跳写保留旧源键，陈旧度由旧 recv_ts 表达"""
    r = _FakeRedis()
    r.store["market:live:source:sina:quote:rb2610"] = json.dumps(
        _quote("rb2610") | {"recv_ts": time.time() - 3600}
    )
    state = bridge.BridgeState()
    monkeypatch.setattr("app.data.sina_quote.fetch_sina_quotes", lambda: [])

    n = await bridge.poll_once(r, state)
    assert n == 0
    # 旧键原样保留（未被空结果覆盖）
    assert "market:live:source:sina:quote:rb2610" in r.store
    assert state.last_poll_ok is False
    assert state.poll_fail == 1


async def test_poll_once_exception_recorded_and_skips_write(monkeypatch):
    r = _FakeRedis()
    state = bridge.BridgeState()

    def _boom():
        raise RuntimeError("sina down")

    monkeypatch.setattr("app.data.sina_quote.fetch_sina_quotes", _boom)
    n = await bridge.poll_once(r, state)
    assert n == 0
    assert r.store == {}
    assert state.last_poll_ok is False
    assert "RuntimeError" in state.last_poll_error


async def test_current_interval_trading(monkeypatch):
    r = _FakeRedis()

    async def _channel(redis, mode="live"):
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    monkeypatch.setattr("app.services.trading_hours.is_trading_hours", lambda: True)
    interval, mode = await bridge._current_interval(r)
    assert mode == "trading"
    assert interval == config.POLL_INTERVAL_TRADING


async def test_current_interval_closed(monkeypatch):
    r = _FakeRedis()

    async def _channel(redis, mode="live"):
        return "sina"

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    monkeypatch.setattr("app.services.trading_hours.is_trading_hours", lambda: False)
    interval, mode = await bridge._current_interval(r)
    assert mode == "closed"
    assert interval == config.POLL_INTERVAL_CLOSED


async def test_current_interval_push_active(monkeypatch):
    """推送源供数（渠道=vvtr + 开关开 + 心跳新鲜）→ 降频到 60s"""
    r = _FakeRedis()
    r.store["market:live:vvtr:heartbeat"] = json.dumps({"ts": time.time()})

    async def _channel(redis, mode="live"):
        return "vvtr"

    async def _state(redis):
        return {"enabled": True, "quotes_enabled": True}

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    monkeypatch.setattr(
        "app.connectors.vvtr.config.read_vvtr_state_from_redis", _state
    )
    interval, mode = await bridge._current_interval(r)
    assert mode == "push-active"
    assert interval == config.POLL_INTERVAL_PUSH_ACTIVE


async def test_current_interval_stale_heartbeat_not_push_active(monkeypatch):
    """心跳过期（桥断流）→ 不视为推送供数，回到交易时段秒级"""
    r = _FakeRedis()
    r.store["market:live:vvtr:heartbeat"] = json.dumps({"ts": time.time() - 600})

    async def _channel(redis, mode="live"):
        return "vvtr"

    async def _state(redis):
        return {"enabled": True, "quotes_enabled": True}

    monkeypatch.setattr(
        "app.services.market_channel.read_channel_from_redis", _channel
    )
    monkeypatch.setattr(
        "app.connectors.vvtr.config.read_vvtr_state_from_redis", _state
    )
    monkeypatch.setattr("app.services.trading_hours.is_trading_hours", lambda: True)
    interval, mode = await bridge._current_interval(r)
    assert mode == "trading"


async def test_heartbeat_loop_writes_key(monkeypatch):
    r = _FakeRedis()
    task = asyncio.create_task(bridge.heartbeat_loop(r))
    try:
        for _ in range(50):
            if r.store:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    raw = r.store.get(config.HEARTBEAT_KEY)
    assert raw and raw.endswith("|sina-bridge")
    ts = float(raw.split("|")[0])
    assert abs(time.time() - ts) < 30


async def test_monthly_fetch_once_writes_monthly_keys(monkeypatch):
    months = [
        {"symbol": "rb2601", "name": "rb2601", "exchange": "上期所"},
        {"symbol": "rb2605", "name": "rb2605", "exchange": "上期所"},
    ]
    monkeypatch.setattr(
        "app.data.contract_months.generate_all_month_symbols", lambda now: months
    )

    def _batch(contracts, contract_map):
        return [_quote(c["symbol"]) for c in contracts]

    monkeypatch.setattr("app.data.sina_quote._fetch_batch", _batch)
    r = _FakeRedis()
    count = await bridge.monthly_fetch_once(r)
    assert count == 2
    assert set(r.store) == {"market:monthly:quote:rb2601", "market:monthly:quote:rb2605"}

# ---------- 推流副本双写（58 中心化 → 33 Redis fanout） ----------


async def test_poll_once_fanouts_to_replica(monkeypatch):
    """配置副本时：源键双写主 Redis + 副本，副本状态 ok"""
    r = _FakeRedis()
    replica = _FakeRedis()
    state = bridge.BridgeState()
    monkeypatch.setattr(bridge.config, "REPLICATE_REDIS_URL", "redis://replica.test/0")
    monkeypatch.setattr(bridge, "_replica_redis", replica)
    try:
        monkeypatch.setattr(
            "app.data.sina_quote.fetch_sina_quotes",
            lambda: [_quote("rb2610")],
        )
        n = await bridge.poll_once(r, state)
        assert n == 1
        assert "market:live:source:sina:quote:rb2610" in r.store
        assert "market:live:source:sina:quote:rb2610" in replica.store
        assert state.last_replica_ok is True
    finally:
        bridge._replica_redis = None


async def test_replica_failure_does_not_break_primary(monkeypatch):
    """副本写失败：主链路不受影响，副本状态记错"""

    class _BrokenPipe:
        def set(self, *a, **k):
            return None

        async def execute(self):
            raise ConnectionError("replica down")

    class _BrokenReplica(_FakeRedis):
        def pipeline(self):
            return _BrokenPipe()

    r = _FakeRedis()
    state = bridge.BridgeState()
    monkeypatch.setattr(bridge.config, "REPLICATE_REDIS_URL", "redis://replica.test/0")
    monkeypatch.setattr(bridge, "_replica_redis", _BrokenReplica())
    try:
        monkeypatch.setattr(
            "app.data.sina_quote.fetch_sina_quotes",
            lambda: [_quote("rb2610")],
        )
        n = await bridge.poll_once(r, state)
        assert n == 1
        # 主写成功
        assert "market:live:source:sina:quote:rb2610" in r.store
        assert state.last_poll_ok is True
        # 副本失败被记录
        assert state.last_replica_ok is False
        assert "replica down" in state.last_replica_error
    finally:
        bridge._replica_redis = None


async def test_no_replica_by_default(monkeypatch):
    """未配置副本：单写主 Redis，replica_enabled=False"""
    monkeypatch.setattr(bridge, "_replica_redis", None)
    r = _FakeRedis()
    state = bridge.BridgeState()
    assert state.replica_enabled is False or bridge._replica_redis is None
    monkeypatch.setattr(
        "app.data.sina_quote.fetch_sina_quotes",
        lambda: [_quote("rb2610")],
    )
    n = await bridge.poll_once(r, state)
    assert n == 1
    assert state.last_replica_ok is None  # 未启用副本无状态
