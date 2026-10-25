"""K 线多周期打包端点测试

验证 GET /api/market/kline/bundle：
- 五分钟周期各取数一次且参数正确透传
- 响应结构 / has_more 判定与单周期端点一致
- symbol 空 400
- 单周期取数异常归一为空 bars，不影响其余周期
"""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


def _bar(time: str, close: float = 100.0) -> dict[str, Any]:
    return {
        "time": time,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 10,
        "settle": None,
        "open_interest": None,
    }


@pytest.fixture()
def bundle_app(monkeypatch):
    from app.core.deps import get_session
    from app.main import app

    calls: list[dict[str, Any]] = []

    async def _mock_history(redis_conn, symbol, period, limit=0,
                            end_time="", session=None,
                            trading_mode="live", adjusted=False):
        calls.append(
            {
                "symbol": symbol,
                "period": period,
                "limit": limit,
                "end_time": end_time,
                "trading_mode": trading_mode,
            }
        )
        # 每周期 3 根 bar；limit=2 时由真实实现的调用方切片语义模拟——
        # 这里直接按 limit 截断，使 has_more 判定可测
        bars = [_bar(f"2026-08-25 09:{i:02d}") for i in range(3)]
        return bars[:limit] if limit else bars

    monkeypatch.setattr(
        "app.api.kline.get_kline_history", _mock_history
    )

    async def _mock_session():
        yield None

    app.dependency_overrides[get_session] = _mock_session
    app.state.redis = object()  # get_kline_history 已 mock，redis 不被使用
    yield app, calls
    app.dependency_overrides.clear()


async def test_bundle_returns_five_minute_periods(bundle_app):
    """五分钟周期各取一次，参数透传正确，响应结构完整"""
    app, calls = bundle_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/kline/bundle", params={"symbol": "rb2610", "limit": 3}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "rb2610"
    assert [p["period"] for p in body["periods"]] == [
        "1m", "5m", "15m", "30m", "60m",
    ]
    for seg in body["periods"]:
        assert len(seg["bars"]) == 3
        assert seg["has_more"] is True  # len(bars) == limit
    assert len(calls) == 5
    assert all(c["symbol"] == "rb2610" for c in calls)
    assert all(c["limit"] == 3 for c in calls)
    assert all(c["trading_mode"] == "live" for c in calls)  # 无 token 默认 live


async def test_bundle_has_more_false_when_under_limit(bundle_app):
    """返回根数低于 limit 时 has_more=False"""
    app, _ = bundle_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/market/kline/bundle", params={"symbol": "rb2610", "limit": 200}
        )
    assert resp.status_code == 200
    for seg in resp.json()["periods"]:
        assert seg["has_more"] is False


async def test_bundle_empty_symbol_rejected(bundle_app):
    """symbol 空 → 400"""
    app, _ = bundle_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/market/kline/bundle", params={"symbol": ""})
    assert resp.status_code == 400


async def test_bundle_single_period_failure_isolated(monkeypatch):
    """单周期取数异常归一为空 bars，其余周期正常"""
    from app.core.deps import get_session
    from app.main import app

    async def _mock_history(redis_conn, symbol, period, limit=0,
                            end_time="", session=None,
                            trading_mode="live", adjusted=False):
        if period == "30m":
            raise RuntimeError("source down")
        return [_bar(f"2026-08-25 09:{period}")]

    monkeypatch.setattr("app.api.kline.get_kline_history", _mock_history)

    async def _mock_session():
        yield None

    app.dependency_overrides[get_session] = _mock_session
    app.state.redis = object()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/market/kline/bundle", params={"symbol": "rb2610"}
            )
        assert resp.status_code == 200
        segs = {p["period"]: p for p in resp.json()["periods"]}
        assert segs["30m"]["bars"] == []
        for p in ("1m", "5m", "15m", "60m"):
            assert len(segs[p]["bars"]) == 1
    finally:
        app.dependency_overrides.clear()
