"""relay 节奏测试：单飞合并 / 429 防护窗口 / 429 不入缓存 / WS 日志过滤

openspec vvtr-relay-pacing。压测背景：6 客户端冷启 125s、20% 429——
放大器是同参并发各自消耗 1rps 预算；且文档警告多次超频进临时黑名单。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import app.relay.main as relay
from app.relay.main import RelaySettings, create_app

OK_BODY = b'{"code":200,"data":{"list":[]}}'
R429_BODY = b'{"code":429,"msg":"rate limit"}'
URL = (
    "/v1/kline/history?symbols=rb2610&interval=1m"
    "&from=2026-08-25%2000:00:00&to=2026-08-25%2010:00:00&apiKey=k"
)


def test_singleflight_concurrent_one_forward(monkeypatch):
    calls: list[int] = []

    def fake_forward(settings_, sub, params, rate_):
        calls.append(1)
        time.sleep(0.4)  # 让并发全部落在 in-flight 窗口内
        return relay.CachedResponse(200, "application/json", OK_BODY)

    monkeypatch.setattr(relay, "_forward_sync", fake_forward)
    app = create_app(RelaySettings(api_key="k"), start_upstream=False)
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=5) as pool:
            rs = list(pool.map(lambda _: client.get(URL), range(5)))
        stats = client.get("/stats").json()
    assert all(r.status_code == 200 for r in rs)
    assert all(r.content == OK_BODY for r in rs)
    assert len(calls) == 1  # 5 路并发只出网一次
    assert stats["singleflight_hits"] == 4
    assert stats["http_forwarded"] == 1


def test_429_not_cached_and_pacing_window(monkeypatch):
    calls: list[int] = []

    def fake_429(settings_, sub, params, rate_):
        calls.append(1)
        return relay.CachedResponse(429, "application/json", R429_BODY)

    monkeypatch.setattr(relay, "_forward_sync", fake_429)
    app = create_app(RelaySettings(api_key="k"), start_upstream=False)
    ctx = app.state.relay
    with TestClient(app) as client:
        # 3 次 429：每次都出网（证明 429 响应不入缓存）
        for _ in range(3):
            r = client.get(URL)
            assert r.status_code == 429
        assert len(calls) == 3

        # 第 4 次：防护窗口开启 → 直接 503，不出网
        r4 = client.get(URL)
        assert r4.status_code == 503
        assert b"pacing window" in r4.content
        assert len(calls) == 3

        stats = client.get("/stats").json()
        assert stats["upstream_429"] == 3
        assert stats["pacing_rejects"] == 1

        # 窗口结束（手动清零不等 5s）→ 恢复出网
        ctx.pacing.clear()

        def fake_ok(settings_, sub, params, rate_):
            calls.append(1)
            return relay.CachedResponse(200, "application/json", OK_BODY)

        monkeypatch.setattr(relay, "_forward_sync", fake_ok)
        r5 = client.get(URL)
        assert r5.status_code == 200
        assert len(calls) == 4

        # 成功响应入缓存：再请求一次命中缓存不再出网
        r6 = client.get(URL)
        assert r6.status_code == 200
        assert len(calls) == 4
        assert client.get("/stats").json()["cache_hits"] == 1


def test_pacing_window_leaves_inflight_share(monkeypatch):
    """窗口开启后，已在途的同参请求仍可共享（不出网第二次）"""
    calls: list[int] = []

    def fake(settings_, sub, params, rate_):
        calls.append(1)
        time.sleep(0.3)
        return relay.CachedResponse(200, "application/json", OK_BODY)

    monkeypatch.setattr(relay, "_forward_sync", fake)
    app = create_app(RelaySettings(api_key="k"), start_upstream=False)
    ctx = app.state.relay
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rs = list(pool.map(lambda _: client.get(URL), range(2)))
        # 手动置窗口开启再试一个不同参数
        ctx.pacing._until = time.monotonic() + 60
        r3 = client.get(URL + "&x=1")
        assert rs[0].status_code == rs[1].status_code == 200
        assert r3.status_code == 503
        assert len(calls) == 1


def test_ws_accept_log_filter_drops_apikey_lines():
    f = relay._WsAcceptLogFilter()
    leak = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1,
        '%s - "WebSocket %s" [accepted]',
        ("1.2.3.4:1", "/v1/connect?apiKey=SECRET&type=14"),
        None,
    )
    assert f.filter(leak) is False  # 含 apiKey 的 WS 行被丢弃

    normal = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1,
        "Application startup complete.", (), None,
    )
    assert f.filter(normal) is True

    # 幂等安装不炸
    relay.install_ws_log_filter()
    relay.install_ws_log_filter()
    assert any(
        isinstance(x, relay._WsAcceptLogFilter)
        for x in logging.getLogger("uvicorn.error").filters
    )
