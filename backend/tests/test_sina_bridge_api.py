"""sina-bridge HTTP 面单测（start_loops=False，不起轮询任务）"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.connectors.sina.api import create_app


class _FakeRedis:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    async def ping(self):
        if not self.ok:
            raise ConnectionError("redis down")
        return True

    async def aclose(self):
        return None


def test_health_ok(monkeypatch):
    app = create_app(start_loops=False)

    class _FakeRedisFactory:
        @staticmethod
        def create():
            return _FakeRedis(ok=True)

    # 直接注入：lifespan 里 _create_redis 的结果在启动后挂 app.state
    with TestClient(app) as client:
        app.state.redis = _FakeRedis(ok=True)
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["redis"] is True


def test_health_redis_down(monkeypatch):
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        app.state.redis = _FakeRedis(ok=False)
        resp = client.get("/health")
        assert resp.status_code == 503


def test_kline_minute_proxies(monkeypatch):
    bars = [{"time": "2026-08-24 09:31:00", "open": 3000, "close": 3010}]
    monkeypatch.setattr(
        "app.data.sina_kline.fetch_minute_kline_strict",
        lambda symbol, period: bars,
    )
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        resp = client.get(
            "/kline/minute", params={"symbol": "rb2610", "period": "1m"}
        )
        assert resp.status_code == 200
        assert resp.json() == bars


def test_kline_minute_limit_tail(monkeypatch):
    bars = [
        {"time": f"2026-08-24 09:{i:02d}:00", "open": 3000, "close": 3000 + i}
        for i in (1, 2, 3)
    ]
    monkeypatch.setattr(
        "app.data.sina_kline.fetch_minute_kline_strict", lambda symbol, period: bars
    )
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        resp = client.get(
            "/kline/minute", params={"symbol": "rb2610", "period": "5m", "limit": 1}
        )
        assert resp.status_code == 200
        assert resp.json() == [bars[-1]]


def test_kline_minute_invalid_period_rejected():
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        resp = client.get(
            "/kline/minute", params={"symbol": "rb2610", "period": "7m"}
        )
        assert resp.status_code == 422


def test_kline_daily_proxies(monkeypatch):
    bars = [{"time": "2026-08-23", "open": 3000, "close": 3050}]
    monkeypatch.setattr("app.data.sina_kline.fetch_daily_kline_strict", lambda symbol: bars)
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        resp = client.get("/kline/daily", params={"symbol": "rb2610"})
        assert resp.status_code == 200
        assert resp.json() == bars


def test_quote_on_demand(monkeypatch):
    q = {"symbol": "rb2610", "last_price": 3010.0}
    monkeypatch.setattr("app.data.sina_quote.fetch_sina_quote", lambda symbol: q)
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        resp = client.get("/quote/rb2610")
        assert resp.status_code == 200
        assert resp.json()["last_price"] == 3010.0


def test_quote_not_found(monkeypatch):
    monkeypatch.setattr("app.data.sina_quote.fetch_sina_quote", lambda symbol: None)
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        resp = client.get("/quote/nope2600")
        assert resp.status_code == 404


def test_quotes_snapshot():
    app = create_app(start_loops=False)
    with TestClient(app) as client:
        app.state.sina_state.latest_quotes = [{"symbol": "rb2610"}]
        resp = client.get("/quotes")
        assert resp.status_code == 200
        assert resp.json() == [{"symbol": "rb2610"}]
