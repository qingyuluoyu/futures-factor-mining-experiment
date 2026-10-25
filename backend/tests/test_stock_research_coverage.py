from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.core.deps import AuthContext, get_auth_context
from app.models.kline_history import KlineHistory
from tests.test_stock_research_snapshots import snapshot_database


def coverage_module():
    import app.services.stock_factors.coverage as module

    return module


@pytest.mark.asyncio
async def test_coverage_aggregates_real_stock_rows_without_fabricating_missing_prices():
    async with snapshot_database(missing_price=True) as (factory, database):
        result = await coverage_module().get_research_coverage(factory, today=date(2024, 1, 3))
    assert result["catalog_count"] == 2
    assert result["available"] == {"first_trade_date": "2024-01-02", "last_trade_date": "2024-01-03", "securities": 2, "price_rows": 3, "observed_dates": 2}
    assert result["recommended"] == result["requested"] == {"start_date": "2024-01-02", "end_date": "2024-01-03"}
    assert result["selected"] == {"securities": 2, "observed_dates": 2, "price_rows": 3, "missing_price_cells": 1, "valuation_rows": 4, "financial_rows": 2, "panel_cells": 36}
    assert result["status"] == "within_limits" and result["market"] == "stock"
    assert result["warnings"] and result["adjustment"] == "forward_adjusted_unversioned"


@pytest.mark.asyncio
async def test_default_coverage_uses_latest_61_observations_and_excludes_future_dates():
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            for offset in range(80):
                session.add(KlineHistory(market_type="stock", code="600001", trade_date=date(2024, 2, 1) + timedelta(days=offset), open=10, high=11, low=9, close=10, volume=1))
            session.commit()
        result = await coverage_module().get_research_coverage(factory, today=date(2024, 4, 15))
    assert result["recommended"] == {"start_date": "2024-02-15", "end_date": "2024-04-15"}
    assert result["selected"]["observed_dates"] == 61
    assert result["available"]["last_trade_date"] == "2024-04-15"


@pytest.mark.asyncio
async def test_requested_range_empty_data_and_snapshot_limit_are_explicit(monkeypatch):
    module = coverage_module()
    async with snapshot_database() as (factory, database):
        empty = await module.get_research_coverage(factory, date(2024, 2, 1), date(2024, 2, 2), today=date(2024, 4, 1))
        assert empty["status"] == "no_data" and empty["selected"]["price_rows"] == 0
        assert empty["available"]["price_rows"] == 4
        monkeypatch.setattr(module, "MAX_PANEL_CELLS", 10)
        limited = await module.get_research_coverage(factory, today=date(2024, 1, 3))
        assert limited["status"] == "over_limit" and limited["selected"]["panel_cells"] == 36
        assert limited["limits"]["max_panel_cells"] == 10


@pytest.mark.asyncio
async def test_no_stock_prices_has_null_ranges_not_a_fake_recent_period():
    async with snapshot_database() as (factory, database):
        with Session(database) as session:
            session.query(KlineHistory).filter(KlineHistory.market_type == "stock").delete()
            session.commit()
        result = await coverage_module().get_research_coverage(factory, today=date(2024, 1, 3))
    assert result["status"] == "no_data" and result["recommended"] is None and result["requested"] is None
    assert result["available"]["first_trade_date"] is None and result["available"]["securities"] == 0
    assert result["selected"]["panel_cells"] == 0 and result["catalog_count"] == 2


@pytest.mark.asyncio
async def test_coverage_api_requires_authentication_and_rejects_bad_or_future_dates():
    from app.api.stock_research_jobs import router

    async with snapshot_database() as (factory, database):
        app = FastAPI()
        app.include_router(router)
        app.state.session_factory = factory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.get("/api/stock/research/coverage")
            assert denied.status_code == 401
            app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=uuid4(), market_type="stock"), trading_mode="virtual")
            for suffix in ("?start_date=bad", "?start_date=2026-09-01&end_date=2026-01-01", "?end_date=2999-01-01"):
                response = await client.get("/api/stock/research/coverage" + suffix)
                assert response.status_code == 422 and response.headers.get("X-Request-ID")
            response = await client.get("/api/stock/research/coverage?start_date=2024/01/02&end_date=20240103")
            assert response.status_code == 200 and response.headers.get("Cache-Control") == "no-store"
            assert response.headers.get("X-Request-ID") and response.json()["selected"]["price_rows"] == 4
