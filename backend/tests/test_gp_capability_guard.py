from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.factor_mining import router
from app.core.deps import get_current_user


@pytest.mark.asyncio
async def test_unconnected_checkpoint_worker_cannot_create_or_resume_legacy_tasks():
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="fixture", market_type="futures")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        supported = (await client.get("/api/factor-mining/supported")).json()
        assert supported["accepting"] is False and supported["exact_resume_available"] is False
        assert supported["checkpoint_kernel_version"] == "gp-stepwise-checkpoint-v1"
        assert "2005" not in str(supported)
        for path, body in [("/api/factor-mining/tasks", {"symbol": "rb2701"}), ("/api/factor-mining/tasks/00000000-0000-0000-0000-000000000001/resume", None)]:
            result = await client.post(path, json=body)
            assert result.status_code == 503
            assert result.headers["X-Research-Error"] == "checkpoint_worker_not_connected"
            assert result.headers["Cache-Control"] == "no-store" and result.headers["X-Request-ID"]
        identifier = "00000000-0000-0000-0000-000000000001"
        for method, path in [("GET", "/tasks"), ("GET", f"/tasks/{identifier}"), ("GET", f"/tasks/{identifier}/champions"), ("DELETE", f"/tasks/{identifier}"), ("POST", f"/tasks/{identifier}/pause"), ("POST", f"/tasks/{identifier}/cancel")]:
            result = await client.request(method, "/api/factor-mining" + path)
            assert result.status_code == 503 and result.headers["X-Research-Error"] == "checkpoint_worker_not_connected"


@pytest.mark.asyncio
async def test_mining_capability_still_requires_authentication_and_futures_market():
    application = FastAPI()
    application.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        assert (await client.get("/api/factor-mining/supported")).status_code == 401
        application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="fixture", market_type="stock")
        response = await client.get("/api/factor-mining/supported")
        assert response.status_code == 409 and response.headers["X-Market-Capability"] == "futures_required"


@pytest.mark.asyncio
async def test_unconnected_legacy_loop_does_not_acquire_database_or_download_prices():
    from app.services.factor_mining.loop import _process_pending_and_running

    def forbidden_factory():
        raise AssertionError("Unconnected worker must never advance unscoped legacy tasks")
    assert await _process_pending_and_running(forbidden_factory, object()) == 0
