"""成交量分布 API 契约测试 —— 依赖覆盖 + 内存 FakeSession（只测路由/schema 层）

聚合 SQL 本身依赖真库 GROUP BY，FakeSession 直接返回聚合后的行，
验证端点参数校验、buy/sell_total 派生与响应结构。
"""

from __future__ import annotations

from collections import namedtuple
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.user import User

_FakeRow = namedtuple(
    "_FakeRow",
    [
        "price",
        "open_long",
        "close_long",
        "open_short",
        "close_short",
        "total",
        "asof",
    ],
)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """只支持 volume_profile_ticks 的聚合查询形态"""

    def __init__(self, rows: list[_FakeRow]) -> None:
        self.rows = rows

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self.rows)


def _make_user() -> User:
    return User(
        id=uuid4(),
        username="vp-tester",
        hashed_password="x",
        role="user",
        status="active",
    )


def _get_test_app(session: FakeSession) -> Any:
    from app.core.deps import AuthContext, get_auth_context, get_session
    from app.main import app

    async def _mock_session():
        yield session

    async def _mock_ctx():
        return AuthContext(user=_make_user(), trading_mode="live")

    app.dependency_overrides[get_session] = _mock_session
    app.dependency_overrides[get_auth_context] = _mock_ctx
    return app


def _clear_overrides() -> None:
    from app.main import app

    app.dependency_overrides.clear()


async def test_volume_profile_returns_aggregated_rows():
    """聚合行 → 响应含四路开平 + 派生 buy/sell_total"""
    asof = datetime(2026, 9, 16, 6, 30, 0, tzinfo=timezone.utc)  # 北京 14:30
    session = FakeSession(
        [
            _FakeRow(3249.0, 1000, 30, 60, 940, 2030, asof),
            _FakeRow(3250.0, 4000, 100, 20, 60, 4180, asof),
        ]
    )
    app = _get_test_app(session)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/users/me/big-orders/volume-profile", params={"symbol": "rb2610"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "rb2610"
        assert data["trading_day"] == str(date(2026, 9, 16)) or isinstance(
            data["trading_day"], str
        )
        # asof 序列化为北京时间（+08:00）
        assert data["asof"].startswith("2026-09-16T14:30")
        # 最新成交价锚点（FakeSession 第二次查询返回聚合行首行 price）
        assert data["last_price"] == 3249.0
        rows = data["rows"]
        assert len(rows) == 2
        top = rows[1]  # 3250 档
        assert top["price"] == 3250.0
        assert top["open_long"] == 4000
        assert top["close_short"] == 60
        assert top["buy_total"] == 4000 + 60
        assert top["sell_total"] == 20 + 100
        assert top["total"] == 4180
    finally:
        _clear_overrides()


async def test_volume_profile_empty_symbol_rejected():
    """symbol 必填（min_length=1）→ 422"""
    session = FakeSession([])
    app = _get_test_app(session)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/users/me/big-orders/volume-profile")
        assert resp.status_code == 422
    finally:
        _clear_overrides()


async def test_volume_profile_empty_day_returns_empty_rows():
    """无数据（清空后/未开盘）→ rows=[]，asof=None"""
    session = FakeSession([])
    app = _get_test_app(session)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/users/me/big-orders/volume-profile", params={"symbol": "au2612"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rows"] == []
        assert data["asof"] is None
    finally:
        _clear_overrides()
