from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.users import router
from app.core.deps import AuthContext, get_auth_context, get_current_user, get_session


@pytest.mark.asyncio
async def test_profile_update_keeps_authenticated_trading_mode_and_market():
    user = SimpleNamespace(id=uuid4(), username="synthetic_profile", phone=None, email=None, avatar=None, real_name_verified=False,
                           role="user", status="active", market_type="stock", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    session = AsyncMock()
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_auth_context] = lambda: AuthContext(user, "virtual")
    application.dependency_overrides[get_current_user] = lambda: user

    async def database():
        yield session

    application.dependency_overrides[get_session] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.put("/api/users/me", json={"avatar": "https://example.test/avatar.png"})
    assert response.status_code == 200
    assert response.json()["trading_mode"] == "virtual"
    assert response.json()["market_type"] == "stock"
    assert response.json()["id"] == str(user.id)
    assert response.json()["avatar"] == "https://example.test/avatar.png"
    assert len(response.headers["X-Request-ID"]) == 32
    assert response.headers["Cache-Control"] == "no-store"
