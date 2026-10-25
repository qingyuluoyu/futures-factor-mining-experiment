from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.stock_replay import router
from app.core.deps import get_auth_context, get_session, get_settings


@pytest.mark.asyncio
async def test_disabled_replay_never_accesses_unmigrated_tables():
    application = FastAPI()
    application.include_router(router)
    session = AsyncMock()
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_replay_enabled=False)
    application.dependency_overrides[get_auth_context] = lambda: SimpleNamespace(user=SimpleNamespace(id="qa", market_type="stock"), trading_mode="virtual")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        result = await client.get("/api/stock/replay/availability")
        assert result.json() == {"enabled": False, "datasets": []}
        assert (await client.get("/api/stock/replay/accounts")).status_code == 503
    session.execute.assert_not_called()
    session.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_replay_requires_authentication():
    application = FastAPI()
    application.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        assert (await client.get("/api/stock/replay/availability")).status_code == 401
