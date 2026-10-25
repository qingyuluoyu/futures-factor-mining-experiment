from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.stock_research_job import ResearchJobBase, StockResearchJob
from app.models.user import User
from app.schemas.stock_research_jobs import CreateResearchJob
from app.services.stock_research_jobs import JobError, ResearchJobService, claim_next_job, finish_job, heartbeat_job, recover_expired_jobs, release_worker_slot


@asynccontextmanager
async def job_database():
    engine = create_engine("sqlite:///:memory:")
    User.__table__.create(engine)
    ResearchJobBase.metadata.create_all(engine)

    class DatabaseSession:
        def __init__(self):
            self.sync = Session(engine, expire_on_commit=False)
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.sync.close()

        def get_bind(self):
            return self.sync.get_bind()

        def add(self, item):
            self.sync.add(item)

        async def execute(self, statement):
            self.statements.append(str(statement.compile()))
            return self.sync.execute(statement)

        async def scalar(self, statement):
            self.statements.append(str(statement.compile()))
            return self.sync.scalar(statement)

        async def flush(self):
            self.sync.flush()

        async def commit(self):
            self.sync.commit()

        async def rollback(self):
            self.sync.rollback()

    owner, other = uuid4(), uuid4()
    async with DatabaseSession() as session:
        session.add(User(id=owner, username="qa_jobs_owner", hashed_password="not-a-login"))
        session.add(User(id=other, username="qa_jobs_other", hashed_password="not-a-login"))
        await session.commit()
    try:
        yield DatabaseSession, owner, other
    finally:
        engine.dispose()


def creation(**changes):
    return TypeAdapter(CreateResearchJob).validate_python({"kind": "analysis", "idempotency_key": str(uuid4()), "parameters": {"factor_id": "ret_20d", "start_date": "20260713", "end_date": "20260911"}, **changes})


@pytest.mark.asyncio
async def test_job_creation_idempotency_permissions_and_queue_backpressure():
    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        body = creation()
        job = await service.create(body, request_id="a" * 32)
        assert job["status"] == "pending" and not job["has_result"] and job["attempts"] == 0
        assert (await service.create(body, request_id="b" * 32))["id"] == job["id"]
        with pytest.raises(JobError) as conflict:
            await service.create(creation(idempotency_key=body.idempotency_key, title="不同任务"), request_id="c" * 32)
        assert conflict.value.status == 409
        with pytest.raises(JobError) as full:
            await service.create(creation(), request_id="d" * 32)
        assert full.value.status == 429
        for user_id, mode in [(other, "virtual"), (owner, "live")]:
            with pytest.raises(JobError) as denied:
                await ResearchJobService(session, user_id, mode).get(UUID(job["id"]))
            assert denied.value.status == 404
        assert (await service.list(limit=20, offset=0))["total"] == 1
        assert (await ResearchJobService(session, other, "virtual").list(limit=20, offset=0))["items"] == []


@pytest.mark.asyncio
async def test_non_accepting_worker_still_allows_scoped_idempotent_reconciliation_but_never_new_work():
    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        body = creation()
        original = await service.create(body, request_id="a" * 32)
        await session.commit()
        assert (await service.create(body, request_id="b" * 32, accepting_new=False))["id"] == original["id"]
        for identity, mode in ((owner, "virtual"), (owner, "live"), (other, "virtual")):
            candidate = creation() if (identity, mode) == (owner, "virtual") else body
            with pytest.raises(JobError) as blocked:
                await ResearchJobService(session, identity, mode).create(candidate, request_id="c" * 32, accepting_new=False)
            assert blocked.value.code == "worker_unavailable" and blocked.value.status == 503
        with pytest.raises(JobError) as conflict:
            await service.create(creation(idempotency_key=body.idempotency_key, title="different"), request_id="d" * 32, accepting_new=False)
        assert conflict.value.code == "idempotency_conflict"
        assert (await service.list(limit=20, offset=0))["total"] == 1


@pytest.mark.asyncio
async def test_cancelled_jobs_never_receive_late_results_and_cancellation_is_idempotent():
    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        created = await service.create(creation(), request_id="a" * 32)
        await session.commit()
        claimed = await claim_next_job(session)
        assert claimed and claimed.attempts == 1 and claimed.status == "running"
        token = claimed.worker_token
        await session.commit()
        cancelled = await service.cancel(claimed.id)
        await session.commit()
        assert cancelled["status"] == "cancelled" and cancelled["finished_at"]
        assert (await service.cancel(claimed.id))["status"] == "cancelled"
        assert not await finish_job(session, claimed.id, token, result={"nav": []}, manifest={"data_version": "a" * 64})
        assert (await service.get(claimed.id))["status"] == "cancelled"
        with pytest.raises(JobError) as unavailable:
            await service.result(UUID(created["id"]))
        assert unavailable.value.status == 409


@pytest.mark.asyncio
async def test_completed_report_is_immutable_and_restores_original_result_fields():
    from app.services.stock_factors.engine import METHOD_VERSION

    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        await service.create(creation(), request_id="a" * 32)
        await session.commit()
        claimed = await claim_next_job(session)
        result = {"factor_id": "ret_20d", "method_version": METHOD_VERSION, "validation_status": "research_only", "long_short_nav": [{"date": "2026-08-10", "nav": 1.01}]}
        assert await finish_job(session, claimed.id, claimed.worker_token, result=result, manifest={"data_version": "a" * 64, "source": "真实隔离SQL夹具"})
        await session.commit()
        report = await service.result(claimed.id)
        assert report["long_short_nav"] == result["long_short_nav"]
        assert report["data_version"] == "a" * 64 and report["research_job_id"] == str(claimed.id)
        assert not await finish_job(session, claimed.id, claimed.worker_token, result={**result, "long_short_nav": []}, manifest={"data_version": "b" * 64})
        with pytest.raises(JobError) as terminal:
            await service.cancel(claimed.id)
        assert terminal.value.status == 409


@pytest.mark.asyncio
async def test_worker_claims_one_global_slot_and_expires_stale_leases_without_retry():
    async with job_database() as (factory, owner, other), factory() as session:
        await ResearchJobService(session, owner, "virtual").create(creation(), request_id="a" * 32)
        await ResearchJobService(session, other, "virtual").create(creation(), request_id="b" * 32)
        await session.commit()
        first = await claim_next_job(session)
        await session.commit()
        assert first and await claim_next_job(session) is None
        assert await heartbeat_job(session, first.id, first.worker_token)
        first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        await recover_expired_jobs(session)
        await session.commit()
        assert first.status == "expired" and first.attempts == 1 and first.result_json is None
        assert await claim_next_job(session) is None
        assert await release_worker_slot(session, first.id, first.worker_token)
        second = await claim_next_job(session)
        assert second and second.id != first.id
        assert not await heartbeat_job(session, first.id, first.worker_token)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_first", [True, False])
async def test_cancelled_running_job_keeps_compute_slot_until_actual_process_stop_is_acknowledged(owner_first, monkeypatch):
    import app.services.stock_research_jobs as module

    clock = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "utc_now", lambda: clock)
    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        owner_created = await service.create(creation(), request_id="a" * 32)
        other_service = ResearchJobService(session, other, "virtual")
        other_created = await other_service.create(creation(), request_id="b" * 32)
        owner_job = await service.row(UUID(owner_created["id"]))
        other_job = await other_service.row(UUID(other_created["id"]))
        owner_job.id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-00000000000" + ("1" if owner_first else "2"))
        other_job.id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-00000000000" + ("2" if owner_first else "1"))
        await session.commit()
        first = await claim_next_job(session)
        await session.commit()
        assert first.user_id == (owner if owner_first else other)
        token = first.worker_token
        wrong_owner = other if first.user_id == owner else owner
        with pytest.raises(JobError) as denied:
            await ResearchJobService(session, wrong_owner, "virtual").cancel(first.id)
        assert denied.value.status == 404
        with pytest.raises(JobError) as wrong_mode:
            await ResearchJobService(session, first.user_id, "live").cancel(first.id)
        assert wrong_mode.value.status == 404
        assert first.status == "running" and first.worker_token == token
        await ResearchJobService(session, first.user_id, "virtual").cancel(first.id)
        await session.commit()
        assert await claim_next_job(session) is None
        assert not await release_worker_slot(session, first.id, uuid4())
        assert await release_worker_slot(session, first.id, token)
        second = await claim_next_job(session)
        assert second and second.id != first.id


@pytest.mark.asyncio
async def test_history_and_polling_queries_do_not_fetch_large_result_payloads():
    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        created = await service.create(creation(), request_id="a" * 32)
        await session.commit()
        await service.list(limit=50, offset=0)
        assert "result_json" not in session.statements[-1]
        await service.get(UUID(created["id"]))
        assert "result_json" not in session.statements[-1]


@pytest.mark.asyncio
async def test_original_idempotency_key_still_works_when_blank_end_crosses_max_range_at_midnight(monkeypatch):
    import app.services.stock_research_jobs as module

    from zoneinfo import ZoneInfo

    clock = [datetime.now(ZoneInfo("Asia/Shanghai"))]

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or timezone.utc)

    monkeypatch.setattr(module, "datetime", FixedDatetime)
    body = creation(parameters={"factor_id": "ret_20d", "start_date": (clock[0].date() - timedelta(days=3660)).isoformat(), "end_date": ""})
    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        first = await service.create(body, request_id="a" * 32)
        clock[0] += timedelta(days=1)
        assert (await service.create(body, request_id="b" * 32))["id"] == first["id"]


@pytest.mark.asyncio
async def test_existing_idempotent_request_survives_method_and_workflow_upgrade(monkeypatch):
    import app.schemas.stock_research_jobs as schemas
    import app.services.stock_research_jobs as service_module

    async with job_database() as (factory, owner, other), factory() as session:
        service = ResearchJobService(session, owner, "virtual")
        body = creation()
        first = await service.create(body, request_id="a" * 32)
        await session.commit()
        monkeypatch.setattr(schemas, "METHOD_VERSION", "next-method-version")
        monkeypatch.setattr(schemas, "WORKFLOW_VERSION", "next-workflow-version")
        monkeypatch.setattr(service_module, "METHOD_VERSION", "next-method-version")
        monkeypatch.setattr(service_module, "WORKFLOW_VERSION", "next-workflow-version")
        retried = await service.create(body, request_id="b" * 32)
        assert retried["id"] == first["id"] and retried["method_version"] == first["method_version"]
        with pytest.raises(JobError) as conflict:
            await service.create(creation(idempotency_key=body.idempotency_key, title="different payload"), request_id="c" * 32)
        assert conflict.value.code == "idempotency_conflict"
        await service.cancel(UUID(first["id"]))
        next_job = await service.create(creation(), request_id="d" * 32)
        assert next_job["method_version"] == "next-method-version"
        assert next_job["workflow_version"] == "next-workflow-version"
