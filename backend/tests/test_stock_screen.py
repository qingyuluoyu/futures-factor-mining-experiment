from copy import deepcopy

import httpx
import pytest

from tests.test_stock_market_board_api import market_app
from app.core.deps import get_session


@pytest.mark.asyncio
async def test_stock_screen_real_sql_filters_and_preserves_legacy_contract(market_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market_app), base_url="http://test") as client:
        response = await client.get("/api/stock/market/screen?min_close=11&max_close=11&min_change_pct=10&max_change_pct=10&min_volume=100&limit=1")
        assert response.status_code == 200
        payload = response.json()
        assert payload["screen_version"] == "stock-screen-v1"
        assert payload["items"][0]["code"] == "600001"
        assert payload["total"] == 1 and not payload["has_more"]
        assert payload["applied_filters"]["min_close"] == 11
        assert payload["filter_units"]["volume"] == "lot_100_shares"
        assert response.headers.get("X-Request-ID") and response.headers["Cache-Control"] == "no-store"
        missing = await client.get("/api/stock/market/screen?keyword=目录&min_close=0")
        assert missing.json()["items"] == []
        catalog = await client.get("/api/stock/market/screen?keyword=目录")
        assert catalog.json()["items"][0]["close"] is None
        old = await client.get("/api/stock/market/members?keyword=目录")
        assert old.status_code == 200 and "screen_version" not in old.json() and old.json()["items"][0]["close"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["min_close=12&max_close=11", "min_change_pct=10&max_change_pct=-1", "min_close=nan", "max_close=inf", "min_volume=-1", "limit=101", "offset=10001", "unexpected=1"])
async def test_invalid_stock_screen_conditions_are_rejected(market_app, query):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market_app), base_url="http://test") as client:
        response = await client.get("/api/stock/market/screen?" + query)
    assert response.status_code == 422
    assert response.headers.get("X-Request-ID")


def test_zero_changes_remain_valid_and_missing_or_stale_numbers_never_pass():
    from app.schemas.stock_screen import StockScreenFilters
    from app.services.stock_screen import screen_snapshot

    rows = [{"code": str(index), "name": "fixture", "industry": "fixture", "data_available": available, "close": close, "change_pct": change, "volume": volume} for index, available, close, change, volume in [(1, True, 10, 0, 0), (2, False, 10, 1, 100), (3, True, None, None, None), (4, True, 10, float("inf"), 100)]]
    snapshot = {"items": rows, "boards": [], "volume_unit": "lot_100_shares"}
    before = deepcopy(rows)
    filtered = screen_snapshot(snapshot, StockScreenFilters(min_change_pct=0, max_change_pct=0, min_volume=0))
    assert [row["code"] for row in filtered["items"]] == ["1"]
    assert rows == before


def test_stock_screen_pages_bind_the_whole_filtered_snapshot():
    from app.schemas.stock_screen import StockScreenFilters
    from app.services.stock_screen import StockScreenChangedError, screen_snapshot

    rows = [{"code": str(index), "name": "fixture", "industry": "fixture", "data_available": True, "close": 10, "change_pct": 0, "volume": 1} for index in range(2)]
    snapshot = {"items": rows, "boards": [], "volume_unit": "lot_100_shares"}
    first = screen_snapshot(snapshot, StockScreenFilters(limit=1))
    following = StockScreenFilters(limit=1, offset=1, expected_fingerprint=first["screen_fingerprint"])
    assert screen_snapshot(snapshot, following)["screen_fingerprint"] == first["screen_fingerprint"]
    rows[0]["close"] = 11
    with pytest.raises(StockScreenChangedError):
        screen_snapshot(snapshot, following)


@pytest.mark.asyncio
async def test_stock_screen_database_failure_is_explicit_private_and_not_cacheable(market_app):
    class BrokenSession:
        async def execute(self, statement):
            raise RuntimeError("private-fixture-detail")

    market_app.dependency_overrides[get_session] = lambda: BrokenSession()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market_app), base_url="http://test") as client:
        response = await client.get("/api/stock/market/screen")
    assert response.status_code == 503 and response.headers.get("X-Request-ID")
    assert response.headers.get("Cache-Control") == "no-store"
    assert "private-fixture-detail" not in response.text
