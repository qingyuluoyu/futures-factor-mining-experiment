from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.stock_research_jobs import router
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from tests.test_stock_research_job_service import job_database


@pytest.mark.asyncio
async def test_disabled_feature_does_not_enqueue_or_fake_history():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=uuid4(), market_type="stock"), trading_mode="virtual")
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=False, stock_legacy_sync_enabled=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        available = await client.get("/api/stock/research/availability")
        assert available.status_code == 200 and not available.json()["enabled"]
        assert not available.json()["accepting"]
        assert not available.json()["saved_report_artifacts"]
        disabled = await client.get("/api/stock/research/jobs")
        assert disabled.status_code == 503
        assert disabled.headers.get("X-Request-ID") and disabled.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_jobs_api_real_sql_permissions_and_pagination(monkeypatch):
    import app.api.stock_research_jobs as module

    async def ready(session, settings):
        return True

    monkeypatch.setattr(module, "research_ready", ready)
    async with job_database() as (factory, owner, other):
        app = FastAPI()
        app.include_router(router)
        app.state.stock_research_worker = SimpleNamespace(healthy=True)
        current = {"user": owner, "mode": "virtual"}
        app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=current["user"], market_type="stock"), trading_mode=current["mode"])
        app.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=True, stock_legacy_sync_enabled=True)

        async def sessions():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = {"idempotency_key": str(uuid4()), "kind": "analysis", "parameters": {"factor_id": "ret_20d", "start_date": "20260713", "end_date": "20260911"}}
            created = await client.post("/api/stock/research/jobs", json=body)
            assert created.status_code == 202, created.text
            assert created.headers.get("X-Request-ID")
            job_id = created.json()["id"]
            assert (await client.post("/api/stock/research/jobs", json=body)).json()["id"] == job_id
            assert (await client.get("/api/stock/research/jobs?limit=1")).json()["total"] == 1
            assert (await client.get("/api/stock/research/jobs?limit=51")).status_code == 422
            for user_id, mode in ((other, "virtual"), (owner, "live")):
                current.update(user=user_id, mode=mode)
                assert (await client.get(f"/api/stock/research/jobs/{job_id}")).status_code == 404
                assert (await client.post(f"/api/stock/research/jobs/{job_id}/cancel")).status_code == 404
                assert (await client.get(f"/api/stock/research/jobs/{job_id}/result")).status_code == 404
            current.update(user=owner, mode="virtual")
            assert (await client.post(f"/api/stock/research/jobs/{job_id}/cancel")).json()["status"] == "cancelled"
            assert (await client.get(f"/api/stock/research/jobs/{job_id}/result")).status_code == 409
            app.state.stock_research_worker.healthy = False
            replay = await client.post("/api/stock/research/jobs", json=body)
            assert replay.status_code == 202 and replay.json()["id"] == job_id
            assert replay.json()["status"] == "cancelled"
            conflict = await client.post("/api/stock/research/jobs", json={**body, "title": "changed intent"})
            assert conflict.status_code == 409
            unhealthy = await client.post("/api/stock/research/jobs", json={**body, "idempotency_key": str(uuid4())})
            assert unhealthy.status_code == 503
            assert unhealthy.headers.get("X-Request-ID") and unhealthy.headers.get("Cache-Control") == "no-store"


@pytest.mark.asyncio
async def test_saved_report_capability_requires_ready_schema_and_registered_routes(monkeypatch):
    import app.api.stock_research_jobs as module
    from app.api.stock_research_reports import router as reports

    ready_value = True

    async def ready(session, settings):
        return ready_value

    monkeypatch.setattr(module, "research_ready", ready)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=uuid4(), market_type="stock"), trading_mode="virtual")
    app.dependency_overrides[get_session] = lambda: object()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=True, stock_legacy_sync_enabled=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert not (await client.get("/api/stock/research/availability")).json()["saved_report_artifacts"]
        app.include_router(reports)
        assert (await client.get("/api/stock/research/availability")).json()["saved_report_artifacts"]
        ready_value = False
        assert not (await client.get("/api/stock/research/availability")).json()["saved_report_artifacts"]
