"""backend 侧新浪桥 HTTP 客户端单测 —— 错误降级语义（空值返回）"""

from __future__ import annotations

import httpx
import pytest

from app.connectors.sina import client


class _FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_minute_kline_ok(monkeypatch):
    bars = [{"time": "2026-08-24 09:31:00", "open": 3000, "close": 3010}]
    monkeypatch.setattr(
        client.httpx, "get", lambda url, params=None, timeout=None: _FakeResponse(200, bars)
    )
    out = client.fetch_minute_kline_via_bridge("rb2610", "1m")
    assert out == bars


def test_minute_kline_http_error_returns_empty(monkeypatch):
    def _err(url, params=None, timeout=None):
        raise httpx.ConnectError("bridge down")

    monkeypatch.setattr(client.httpx, "get", _err)
    assert client.fetch_minute_kline_via_bridge("rb2610", "1m") == []


def test_daily_kline_bad_json_returns_empty(monkeypatch):
    monkeypatch.setattr(
        client.httpx,
        "get",
        lambda url, params=None, timeout=None: _FakeResponse(200, ValueError("bad json")),
    )
    assert client.fetch_daily_kline_via_bridge("rb2610") == []


def test_quote_ok_and_miss(monkeypatch):
    q = {"symbol": "rb2610", "last_price": 3010.0}
    monkeypatch.setattr(
        client.httpx, "get", lambda url, params=None, timeout=None: _FakeResponse(200, q)
    )
    assert client.fetch_quote_via_bridge("rb2610") == q

    monkeypatch.setattr(
        client.httpx,
        "get",
        lambda url, params=None, timeout=None: _FakeResponse(404, {"detail": "no"}),
    )
    assert client.fetch_quote_via_bridge("nope") is None


def test_non_list_payload_filtered(monkeypatch):
    monkeypatch.setattr(
        client.httpx,
        "get",
        lambda url, params=None, timeout=None: _FakeResponse(200, {"error": "shape"}),
    )
    assert client.fetch_minute_kline_via_bridge("rb2610", "1m") == []
    assert client.fetch_quotes_via_bridge() == []


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("SINA_BRIDGE_BASE_URL", "http://192.0.2.5:9000/")
    assert client.bridge_base_url() == "http://192.0.2.5:9000"
    monkeypatch.delenv("SINA_BRIDGE_BASE_URL")
    assert client.bridge_base_url() == "http://sina-bridge:8110"
