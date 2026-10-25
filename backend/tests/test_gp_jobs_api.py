from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.gp_jobs import router
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from tests.test_gp_checkpoint_store import storage
from tests.test_gp_jobs import prepared


@pytest.mark.asyncio
async def test_disabled_gp_api_never_mints_tasks_or_claims_worker_ready():
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id=uuid4(), market_type="futures"), "virtual")
    application.dependency_overrides[get_session] = lambda: object()
    application.dependency_overrides[get_settings] = lambda: SimpleNamespace(gp_jobs_enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        available = await client.get("/api/factor-mining/v2/availability")
        assert available.status_code == 200 and available.json()["enabled"] is False
        assert available.json()["accepting"] is False and available.json()["source_ingestion_available"] is False
        assert (await client.post("/api/factor-mining/v2/tasks", json={"input_id": str(uuid4()), "idempotency_key": str(uuid4())})).status_code == 503


@pytest.mark.asyncio
async def test_v2_scoped_creation_query_pause_cancel_and_offline_reconciliation(monkeypatch):
    import app.api.gp_jobs as module
    async def ready(session, settings):
        return True
    monkeypatch.setattr(module, "gp_ready", ready)
    owner, other = uuid4(), uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        application = FastAPI()
        application.include_router(router)
        application.state.gp_checkpoint_worker = SimpleNamespace(healthy=True)
        current = {"user": owner, "mode": "virtual"}
        application.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id=current["user"], market_type="futures"), current["mode"])
        application.dependency_overrides[get_settings] = lambda: SimpleNamespace(gp_jobs_enabled=True)
        async def sessions():
            async with factory() as session:
                yield session
        application.dependency_overrides[get_session] = sessions
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            path = "/api/factor-mining/v2/tasks"
            created = await client.post(path, json=body.model_dump(mode="json"))
            assert created.status_code == 202 and created.headers["Cache-Control"] == "no-store" and created.headers["X-Request-ID"]
            identifier = created.json()["id"]
            assert (await client.get(path + "?limit=1")).json()["total"] == 1
            assert (await client.get(path + "?limit=51")).status_code == 422
            for identity, mode in ((other, "virtual"), (owner, "live")):
                current.update(user=identity, mode=mode)
                assert (await client.get(path + "/" + identifier)).status_code == 404
                assert (await client.post(path + "/" + identifier + "/cancel")).status_code == 404
            current.update(user=owner, mode="virtual")
            assert (await client.post(path + "/" + identifier + "/pause")).json()["status"] == "paused"
            assert (await client.post(path + "/" + identifier + "/resume")).json()["status"] == "pending"
            application.state.gp_checkpoint_worker.healthy = False
            replay = await client.post(path, json=body.model_dump(mode="json"))
            assert replay.status_code == 202 and replay.json()["id"] == identifier
            assert (await client.post(path, json={**body.model_dump(mode="json"), "idempotency_key": str(uuid4())})).status_code == 503
            assert (await client.post(path + "/" + identifier + "/cancel")).json()["status"] == "cancelled"
            assert (await client.get(path + "/" + identifier + "/result")).status_code == 409


@pytest.mark.asyncio
async def test_v2_still_requires_real_auth_and_futures_capability():
    application = FastAPI()
    application.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        assert (await client.get("/api/factor-mining/v2/availability")).status_code == 401
        application.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id=uuid4(), market_type="stock"), "virtual")
        assert (await client.get("/api/factor-mining/v2/availability")).status_code == 409
