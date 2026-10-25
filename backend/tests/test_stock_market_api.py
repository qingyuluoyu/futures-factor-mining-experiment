from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.stock import router
from app.core.deps import get_current_user, get_session


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["code=rb2701", "code=600519&limit=0", "code=600519&limit=999999"])
async def test_stock_kline_rejects_invalid_identifiers_and_unbounded_requests(query):
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa", market_type="stock")
    application.dependency_overrides[get_session] = lambda: MagicMock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
        result = await client.get("/api/stock/kline?" + query)
    assert result.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["offset=-1", "limit=3001"])
async def test_stock_universe_rejects_invalid_pagination(query):
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa", market_type="stock")
    application.dependency_overrides[get_session] = lambda: MagicMock()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
        result = await client.get("/api/stock/universe?" + query)
    assert result.status_code == 422


@pytest.mark.asyncio
async def test_paginated_stock_search_keeps_case_insensitive_name_matching():
    application = FastAPI()
    application.include_router(router)
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa", market_type="stock")
    application.dependency_overrides[get_session] = lambda: session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        assert (await client.get("/api/stock/universe?keyword=tcl&limit=500")).status_code == 200
    statement = str(session.execute.call_args.args[0].compile())
    assert "lower(stock_instruments.name)" in statement
