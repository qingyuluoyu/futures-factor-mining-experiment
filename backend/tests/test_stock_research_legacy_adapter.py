from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api import stock_factors, stock_research_jobs
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from tests.test_stock_research_job_service import job_database


@asynccontextmanager
async def adapter_client(monkeypatch, *, legacy_enabled=True):
    async def ready(session, settings):
        return True

    async def forbidden(*args, **kwargs):
        raise AssertionError("Async negotiation must not load prices or run inline calculations")

    monkeypatch.setattr(stock_research_jobs, "research_ready", ready)
    monkeypatch.setattr(stock_factors, "_execute_research", forbidden)
    async with job_database() as (factory, owner, other):
        application = FastAPI()
        application.include_router(stock_factors.router)
        application.include_router(stock_research_jobs.router)
        application.state.stock_research_worker = SimpleNamespace(healthy=True)
        application.state.session_factory = factory
        current = {"user": owner, "mode": "virtual"}
        application.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id=current["user"], market_type="stock"), current["mode"])
        application.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=True, stock_legacy_sync_enabled=legacy_enabled)

        async def sessions():
            async with factory() as session:
                yield session

        application.dependency_overrides[get_session] = sessions
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test") as client:
            yield client, application, current, other


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,path,parameters", [
    ("analysis", "/api/stock/factors/analysis", {"factor_id": "ret_20d"}),
    ("backtest", "/api/stock/backtest", {"factor_ids": ["ret_20d"]}),
])
async def test_explicit_async_legacy_request_uses_the_same_durable_queue_and_idempotency(monkeypatch, kind, path, parameters):
    parameters = {**parameters, "start_date": "20260713", "end_date": "20260911"}
    async with adapter_client(monkeypatch) as (client, application, current, other):
        key = str(uuid4())
        headers = {"Prefer": "respond-async", "Idempotency-Key": key}
        created = await client.post(path, json=parameters, headers=headers)
        assert created.status_code == 202, created.text
        job = created.json()
        assert job["kind"] == kind and job["status"] == "pending" and job["attempts"] == 0
        assert created.headers["Preference-Applied"] == "respond-async"
        assert created.headers["Location"] == f"/api/stock/research/jobs/{job['id']}"
        assert created.headers["Cache-Control"] == "no-store" and created.headers["X-Request-ID"]
        replay = await client.post("/api/stock/research/jobs", json={"kind": kind, "idempotency_key": key, "parameters": parameters})
        assert replay.status_code == 202 and replay.json()["id"] == job["id"]
        blocked = await client.post(path, json=parameters, headers={**headers, "Idempotency-Key": str(uuid4())})
        assert blocked.status_code == 429
        application.state.stock_research_worker.healthy = False
        assert (await client.post(path, json=parameters, headers=headers)).json()["id"] == job["id"]
        conflict = await client.post(path, json={**parameters, "start_date": "20260714"}, headers=headers)
        assert conflict.status_code == 409
        current["user"] = other
        assert (await client.get(created.headers["Location"])).status_code == 404
        assert (await client.post(path, json=parameters, headers=headers)).status_code == 503


@pytest.mark.asyncio
async def test_async_adapter_reuses_injected_request_session_instead_of_nested_pool_checkout(monkeypatch):
    async with adapter_client(monkeypatch) as (client, application, current, other):
        def forbidden_checkout():
            raise AssertionError("A second session checkout would starve a bounded connection pool")

        application.state.session_factory = forbidden_checkout
        response = await client.post("/api/stock/factors/analysis", json={"factor_id": "pb"}, headers={"Prefer": "respond-async", "Idempotency-Key": str(uuid4())})
        assert response.status_code == 202, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("headers,extra", [
    ({"Prefer": "respond-async"}, {}),
    ({"Prefer": "respond-async", "Idempotency-Key": "not-uuid"}, {}),
    ({"Prefer": "wait=999", "Idempotency-Key": str(uuid4())}, {}),
    ({"Idempotency-Key": str(uuid4())}, {}),
    ({"Prefer": "respond-async", "Idempotency-Key": str(uuid4())}, {"confirmed": True}),
])
async def test_invalid_negotiation_or_strict_payload_never_falls_back_to_sync(monkeypatch, headers, extra):
    async with adapter_client(monkeypatch) as (client, application, current, other):
        response = await client.post("/api/stock/factors/analysis", json={"factor_id": "pb", **extra}, headers=headers)
        assert response.status_code == 422, response.text
        assert (await client.get("/api/stock/research/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_retired_sync_rejects_before_calculation_but_explicit_async_still_works(monkeypatch):
    async with adapter_client(monkeypatch, legacy_enabled=False) as (client, application, current, other):
        response = await client.post("/api/stock/factors/analysis", json={"factor_id": "pb"})
        assert response.status_code == 503
        assert response.headers["X-Research-Error"] == "legacy_sync_disabled"
        assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Request-ID"]
        assert "/api/stock/research/jobs" in response.headers["Link"]
        available = (await client.get("/api/stock/research/availability")).json()
        assert available["legacy_sync_enabled"] is False and available["legacy_async_adapter"] is True
        accepted = await client.post("/api/stock/factors/analysis", json={"factor_id": "pb"}, headers={"Prefer": "respond-async", "Idempotency-Key": str(uuid4())})
        assert accepted.status_code == 202
