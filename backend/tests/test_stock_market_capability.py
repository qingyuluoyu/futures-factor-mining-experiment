from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api import stock, stock_market, stock_factors, stock_replay, stock_research_jobs, stock_research_reports
from app.api.paper import router as paper_router
from app.core.deps import get_current_user, get_session


@pytest.mark.asyncio
@pytest.mark.parametrize("router,path,method,body", [
    (stock.router, "/api/stock/universe", "GET", None),
    (stock_market.router, "/api/stock/market/boards", "GET", None),
    (stock_factors.router, "/api/stock/factors", "GET", None),
    (stock_factors.router, "/api/stock/factors/analysis", "POST", {"factor_id": "pb"}),
    (stock_replay.router, "/api/stock/replay/accounts", "GET", None),
    (stock_research_jobs.router, "/api/stock/research/jobs", "GET", None),
    (stock_research_reports.router, "/api/stock/research/compare?left_id=11111111-1111-4111-8111-111111111111&right_id=22222222-2222-4222-8222-222222222222", "GET", None),
])
async def test_futures_session_cannot_invoke_stock_routes_or_reach_business_queries(router, path, method, body):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="fixture", market_type="futures")

    async def forbidden_session():
        raise AssertionError("Market capability must be checked before business queries")

    app.dependency_overrides[get_session] = forbidden_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.request(method, path, json=body)
    assert response.status_code == 409, response.text
    assert response.headers["X-Market-Capability"] == "stock_required"
    assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_stock_session_cannot_create_or_read_a_futures_paper_account():
    app = FastAPI()
    app.include_router(paper_router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="fixture", market_type="stock")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/api/paper/account")
    assert response.status_code == 409
    assert response.headers["X-Market-Capability"] == "futures_required"
