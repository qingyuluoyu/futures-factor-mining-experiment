from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.core.deps import AuthContext, get_auth_context, get_session
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_jobs import setup

BASE = "/api/stock/research/v2"


def application(factory, owner):
    from app.api.stock_dataset_jobs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/stock")
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(context.user, context.mode)

    async def sessions():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = sessions
    return app, context


@pytest.mark.asyncio
async def test_jobs_are_private_opt_in_and_fail_closed_without_schema_or_worker(tmp_path):
    async with job_database() as (factory, owner, other):
        app, context = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            status = await client.get(BASE + "/jobs/availability")
            assert status.status_code == 200 and status.json()["accepting"] is False
            assert status.json()["research_enabled"] is False
            assert (await client.get(BASE + "/jobs")).status_code == 503
            scope, command = await setup(factory, owner, tmp_path / "artifacts")
            blocked = await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))
            assert blocked.status_code == 503 and blocked.json()["detail"]["code"] == "v2_jobs_disabled"
            app.state.stock_v2_jobs_enabled = True
            offline = await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))
            assert offline.status_code == 503 and offline.json()["detail"]["code"] == "worker_unavailable"
            assert (await client.get(BASE + "/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_job_http_submit_receipt_cancel_and_scoped_reads_are_transactional(tmp_path):
    from app.services.stock_research_v2.jobs import acquire_worker
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            await acquire_worker(session, uuid4())
            await session.commit()
        app, context = application(factory, owner)
        app.state.stock_v2_jobs_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))
            assert response.status_code == 202
            receipt = response.json()
            assert receipt["status"] == "pending" and receipt["result"] is None
            assert response.headers["X-Request-ID"] == receipt["request_id"]
            assert response.headers["Cache-Control"] == "no-store"
            assert (await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))).json()["id"] == receipt["id"]
            listed = (await client.get(BASE + "/jobs?limit=1")).json()
            assert listed["total"] == 1 and "parameters" not in listed["items"][0]
            for identity, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = identity, mode
                assert (await client.get(BASE + "/jobs")).json()["items"] == []
                assert (await client.get(BASE + "/jobs/" + receipt["id"])).status_code == 404
                assert (await client.post(BASE + "/jobs/" + receipt["id"] + "/cancel")).status_code == 404
            context.user.id, context.mode = owner, "virtual"
            app.state.stock_v2_jobs_enabled = False
            cancelled = await client.post(BASE + "/jobs/" + receipt["id"] + "/cancel")
            assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
            assert (await client.get(BASE + "/jobs/" + receipt["id"])).json()["parameters"] == command.parameters.model_dump(mode="json")


@pytest.mark.asyncio
async def test_jobs_reject_market_user_overrides_unknown_query_and_unbounded_inputs(tmp_path):
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        app, context = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for query in ("?limit=101", "?owner_id=" + str(other), "?trading_mode=live"):
                invalid = await client.get(BASE + "/jobs" + query)
                assert invalid.status_code == 422
                assert invalid.json()["detail"]["code"] == "invalid_input"
            body = {**command.model_dump(mode="json"), "owner_id": str(other)}
            assert (await client.post(BASE + "/datasets/jobs", json=body)).status_code == 422
            assert (await client.post(BASE + "/jobs/" + str(uuid4()) + "/cancel", json={"mode": "live"})).status_code == 422
            context.user.market_type = "futures"
            assert (await client.get(BASE + "/jobs/availability")).status_code == 409
        app.dependency_overrides.pop(get_auth_context)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(BASE + "/jobs/availability")).status_code == 401


@pytest.mark.asyncio
async def test_job_timeouts_and_response_failures_are_sanitized_not_empty_success(tmp_path, monkeypatch):
    from app.services.stock_research_v2.jobs import JobService
    async with job_database() as (factory, owner, other):
        app, context = application(factory, owner)

        async def timeout(self, **kwargs):
            raise TimeoutError("secret-upstream")

        monkeypatch.setattr(JobService, "list", timeout)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.get(BASE + "/jobs")
            assert failed.status_code == 504 and failed.json()["detail"]["code"] == "v2_job_timeout"
            assert "secret-upstream" not in failed.text
            assert failed.json()["detail"]["request_id"] == failed.headers["X-Request-ID"]

            async def malformed(self, **kwargs):
                return {"items": [], "total": -1, "has_more": False}

            monkeypatch.setattr(JobService, "list", malformed)
            invalid = await client.get(BASE + "/jobs")
            assert invalid.status_code == 503


@pytest.mark.asyncio
async def test_invalid_write_response_rolls_back_creation_and_does_not_claim_success(tmp_path, monkeypatch):
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    original = JobService.submit

    async def corrupt(self, command, *, request_id):
        result = await original(self, command, request_id=request_id)
        return {**result, "status": "completed", "result": None}

    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            await acquire_worker(session, uuid4())
            await session.commit()
        app, context = application(factory, owner)
        app.state.stock_v2_jobs_enabled = True
        monkeypatch.setattr(JobService, "submit", corrupt)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))
            assert response.status_code == 503
            assert (await client.get(BASE + "/jobs")).json()["total"] == 0


def test_job_response_state_machine_rejects_invalid_running_and_completed_claims():
    from app.schemas.stock_dataset_jobs_api import DatasetJobSummary
    from pydantic import ValidationError
    body = {"id": str(uuid4()), "kind": "dataset", "status": "running", "step": "claimed", "attempts": 0, "request_id": "a" * 32,
        "created_at": "2026-10-24T00:00:00Z", "updated_at": "2026-10-24T00:00:00Z", "started_at": None, "finished_at": None,
        "deadline_at": None, "queue_expires_at": "2026-10-24T00:05:00Z", "method_version": "stock-observation-manifest-v1",
        "workflow_version": "stock-v2-dataset-job-v1", "result": None, "error_code": None}
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate(body)
