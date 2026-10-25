"""sina 桥守卫测试：缓存/单飞/熔断/空结果语义（openspec sina-bridge-protection）

压测背景（2026-08-25）：12 并发触发新浪按 IP 限流整源封 6-7 分钟；
守卫后同参并发只出网一次、死合约不出网、上游故障熔断兜底过期副本。
"""

from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from app.connectors.sina import guard as guard_mod
from app.connectors.sina.api import create_app
from app.connectors.sina.guard import KlineGuard
from app.data import sina_kline


def _bars(n: int = 3) -> list[dict]:
    return [
        {"time": f"2026-08-25 09:{i:02d}:00", "open": 3000, "close": 3010}
        for i in range(n)
    ]


def _run(coro):
    return asyncio.run(coro)


# ---------- 端点面：缓存命中不出网 ----------


def test_cache_hit_no_second_outbound(monkeypatch):
    calls: list[tuple] = []

    def fake(symbol, period):
        calls.append((symbol, period))
        return _bars()

    monkeypatch.setattr(sina_kline, "fetch_minute_kline_strict", fake)
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        r1 = client.get("/kline/minute", params={"symbol": "rb2610", "period": "15m"})
        r2 = client.get("/kline/minute", params={"symbol": "rb2610", "period": "15m"})
        stats = client.get("/stats").json()
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json() == _bars()
    assert len(calls) == 1  # 第二次缓存命中，不出网
    assert stats["kline_cache_hits"] == 1
    assert stats["kline_upstream"] == 1


# ---------- 守卫直测：单飞 / 熔断 / 空结果 ----------


def test_singleflight_concurrent_one_outbound(monkeypatch):
    calls: list[str] = []

    def fake(symbol, period):
        calls.append(symbol)
        time.sleep(0.3)  # 模拟出网耗时，让并发全部落在 in-flight 窗口
        return _bars()

    monkeypatch.setattr(sina_kline, "fetch_minute_kline_strict", fake)
    monkeypatch.setattr(guard_mod, "OUTBOUND_INTERVAL", 0.0)

    async def scenario():
        g = KlineGuard()
        try:
            outs = await asyncio.gather(
                *[g.fetch("minute", "rb2610", "15m") for _ in range(12)]
            )
        finally:
            await g.aclose()
        return g, outs

    g, outs = _run(scenario())
    assert len(calls) == 1  # 12 个并发同参只出网一次
    assert all(o[1] is False for o in outs)
    assert all(o[0] == _bars() for o in outs)
    assert g.kline_singleflight == 11


def test_breaker_opens_after_consecutive_failures(monkeypatch):
    calls: list[str] = []

    def fail_always(symbol, period):
        calls.append(symbol)
        raise RuntimeError("sina rejected (rate limit)")

    monkeypatch.setattr(sina_kline, "fetch_minute_kline_strict", fail_always)
    monkeypatch.setattr(guard_mod, "OUTBOUND_INTERVAL", 0.0)

    async def scenario():
        g = KlineGuard()
        try:
            for _ in range(guard_mod.BREAKER_THRESHOLD):
                try:
                    await g.fetch("minute", "rb2610", "15m")
                except guard_mod.BridgeUpstreamError:
                    pass
            assert g.snapshot()["breaker_state"] == "open"
            # 熔断中：无过期副本的 key 直接拒绝（不出网）
            try:
                await g.fetch("minute", "ma2701", "15m")
                raise AssertionError("应抛 BridgeUpstreamError")
            except guard_mod.BridgeUpstreamError:
                pass
            # 熔断中：有过期副本的 key 返回 stale
            g._stale[("minute", "RB2610", "15m")] = _bars()
            bars, stale = await g.fetch("minute", "rb2610", "15m")
            assert stale is True and bars == _bars()
        finally:
            await g.aclose()
        return len(calls), g.kline_breaker_serves

    n_calls, serves = _run(scenario())
    # 阈值次失败 + 0 次熔断期出网（ma2701 与 rb2610 都被拦）
    assert n_calls == guard_mod.BREAKER_THRESHOLD
    assert serves == 1


def test_clean_empty_is_no_data_not_failure(monkeypatch):
    """干净空=合约无数据：短缓存、不计熔断（12 个死合约不开熔断）"""
    calls: list[str] = []

    def fake(symbol, period):
        calls.append(symbol)
        return []

    monkeypatch.setattr(sina_kline, "fetch_minute_kline_strict", fake)
    monkeypatch.setattr(guard_mod, "OUTBOUND_INTERVAL", 0.0)

    async def scenario():
        g = KlineGuard()
        try:
            for i in range(guard_mod.BREAKER_THRESHOLD + 2):
                bars, stale = await g.fetch("minute", f"xx26{i:02d}", "15m")
                assert bars == [] and stale is False
            # 空结果短缓存：同 key 第二次不出网
            bars, _ = await g.fetch("minute", "xx2601", "15m")
            assert bars == []
        finally:
            await g.aclose()
        return g, len(calls)

    g, n_calls = _run(scenario())
    assert g.snapshot()["breaker_state"] == "closed"
    assert g.kline_upstream_fail == 0
    assert n_calls == guard_mod.BREAKER_THRESHOLD + 2  # 无重复出网


# ---------- 端点面：故障降级过期副本 / 503 ----------


def test_endpoint_upstream_error_503_then_stale(monkeypatch):
    def boom(symbol):
        raise RuntimeError("sina down")

    monkeypatch.setattr(sina_kline, "fetch_daily_kline_strict", boom)
    monkeypatch.setattr(guard_mod, "OUTBOUND_INTERVAL", 0.0)
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        r1 = client.get("/kline/daily", params={"symbol": "rb2610"})
        assert r1.status_code == 503  # 无过期副本 → 503
        # 种过期副本：上游故障期间降级返回 + X-Bridge-Stale
        app.state.kline_guard._stale[("daily", "RB2610", "")] = _bars()
        r2 = client.get("/kline/daily", params={"symbol": "rb2610"})
        assert r2.status_code == 200
        assert r2.headers.get("X-Bridge-Stale") == "1"
        assert r2.json() == _bars()


def test_endpoint_pace_timeout_503(monkeypatch):
    """出网排队超预算 → 过期副本兜底，无副本 503"""

    def boom(symbol, period):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(sina_kline, "fetch_minute_kline_strict", boom)
    monkeypatch.setattr(guard_mod, "OUTBOUND_INTERVAL", 0.0)

    async def scenario():
        g = KlineGuard()
        try:
            # 直接占住令牌桶锁模拟排队积压
            await g._pace_lock.acquire()
            g._stale[("minute", "RB2610", "15m")] = _bars()
            try:
                await asyncio.wait_for(g.fetch("minute", "rb2610", "15m"), timeout=10)
                raise AssertionError("应抛 BridgeBusy")
            except guard_mod.BridgeBusy:
                pass
        finally:
            g._pace_lock.release()
            await g.aclose()

    _run(scenario())
