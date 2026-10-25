import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import page, prepare, service
from tests.test_stock_research_job_service import job_database


async def setup(factory, owner, root):
    from app.models.stock_research_v2_job import V2JobBase, V2JobSchemaVersion
    from app.schemas.stock_research_v2 import Scope
    from app.schemas.stock_research_v2_jobs import CreateDatasetJob
    from app.services.stock_research_v2.jobs import JobService, SCHEMA_PATH, SCHEMA_VERSION
    await prepare(factory)
    async with factory() as session:
        session.sync.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
        V2JobBase.metadata.create_all(session.get_bind())
        session.add(V2JobSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
        archive = service(session, owner, root)
        source = await archive.archive_page(page(), idempotency_key=uuid4(), request_id="a" * 32)
        await session.commit()
    command = CreateDatasetJob(idempotency_key=uuid4(), parameters={"source_ids": [source.source_id], "start": "2026-01-05", "end": "2026-01-05", "warmup_start": "2026-01-05", "pool_id": "qa-only", "pool_version": "a" * 64, "instruments": ["000001.SZ"]})
    return Scope(owner_id=owner, trading_mode="virtual"), command


def test_job_contracts_are_bounded_structured_and_only_observation_dataset_kind_is_supported():
    from app.schemas.stock_research_v2_jobs import CreateDatasetJob
    valid = {"idempotency_key": str(uuid4()), "parameters": {"source_ids": [str(uuid4())], "start": "2026-01-05", "end": "2026-01-05", "warmup_start": "2026-01-05", "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"]}}
    assert CreateDatasetJob.model_validate(valid).kind == "dataset"
    for patch in ({"kind": "ml_train"}, {"owner_id": str(uuid4())}, {"max_runtime_seconds": True}, {"max_runtime_seconds": 301}, {"max_queue_seconds": 901}, {"parameters": {**valid["parameters"], "expression": "delete everything"}}):
        with pytest.raises(ValidationError):
            CreateDatasetJob.model_validate({**valid, **patch})


@pytest.mark.asyncio
async def test_list_does_not_load_payloads_and_detail_still_restores_fixed_parameters(tmp_path):
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = JobService(session, scope)
            await acquire_worker(session, uuid4())
            created = await queue.submit(command, request_id="b" * 32)
            await session.commit()
        async with factory() as session:
            queue = JobService(session, scope)
            listed = await queue.list(limit=1, offset=0)
            assert listed["total"] == 1 and not listed["has_more"]
            assert "parameters" not in listed["items"][0]
            assert "stock_research_v2_jobs.parameters" not in session.statements[-1]
            assert (await queue.get(UUID(created["id"])))["parameters"] == command.parameters.model_dump(mode="json")


@pytest.mark.asyncio
async def test_unmigrated_jobs_do_not_create_tables_or_register_worker(tmp_path):
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    async with job_database() as (factory, owner, other), factory() as session:
        queue = JobService(session, Scope(owner_id=owner, trading_mode="virtual"))
        assert await queue.available() is False
        with pytest.raises(JobError) as blocked:
            await acquire_worker(session, uuid4())
        assert blocked.value.status == 503
        assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_submission_needs_live_worker_is_idempotent_scoped_and_checks_source_ownership(tmp_path):
    from app.schemas.stock_research_v2 import Scope
    from app.schemas.stock_research_v2_jobs import CreateDatasetJob
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = JobService(session, scope)
            with pytest.raises(JobError) as offline:
                await queue.submit(command, request_id="b" * 32)
            assert offline.value.code == "worker_unavailable"
            lease = await acquire_worker(session, uuid4())
            created = await queue.submit(command, request_id="b" * 32)
            await session.commit()
            assert created["status"] == "pending" and created["attempts"] == 0 and created["result"] is None
            assert "worker_token" not in created
            assert (await queue.submit(command, request_id="c" * 32))["id"] == created["id"]
            changed = CreateDatasetJob.model_validate({**command.model_dump(), "max_runtime_seconds": 60})
            with pytest.raises(JobError) as conflict:
                await queue.submit(changed, request_id="c" * 32)
            assert conflict.value.code == "idempotency_conflict"
            for identity, mode in ((other, "virtual"), (owner, "live")):
                private = JobService(session, Scope(owner_id=identity, trading_mode=mode))
                assert (await private.list(limit=20, offset=0))["items"] == []
                with pytest.raises(JobError) as denied:
                    await private.get(UUID(created["id"]))
                assert denied.value.status == 404
                with pytest.raises(JobError) as source_denied:
                    await private.submit(command, request_id="c" * 32)
                assert source_denied.value.status == 404
            with pytest.raises(JobError):
                await queue.list(limit=101, offset=0)


@pytest.mark.asyncio
async def test_cancel_keeps_worker_slot_until_ack_and_late_completion_cannot_publish(tmp_path):
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next, release_claim, finish_dataset
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = JobService(session, scope)
            lease = await acquire_worker(session, uuid4())
            created = await queue.submit(command, request_id="b" * 32)
            first = await claim_next(session, lease)
            assert first.job_id == UUID(created["id"]) and first.scope == scope
            assert await claim_next(session, lease) is None
            cancelled = await queue.cancel(first.job_id)
            assert cancelled["status"] == "cancelled"
            assert (await queue.cancel(first.job_id))["status"] == "cancelled"
            assert await claim_next(session, lease) is None
            assert not await finish_dataset(session, first, None)
            assert await release_claim(session, first)
            assert not await release_claim(session, first)
            assert (await queue.get(first.job_id))["result"] is None


@pytest.mark.asyncio
async def test_real_archive_result_is_committed_with_fixed_reference_and_input_binding(tmp_path):
    from app.schemas.stock_research_v2 import DatasetBuild
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next, finish_dataset, release_claim
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = JobService(session, scope)
            lease = await acquire_worker(session, uuid4())
            await queue.submit(command, request_id="b" * 32)
            claim = await claim_next(session, lease)
            archive = service(session, owner, tmp_path / "artifacts")
            different = DatasetBuild.model_validate({**claim.parameters.model_dump(), "pool_id": "qa-wrong-pool"})
            wrong = await archive.build_dataset(different, idempotency_key=uuid4(), request_id="c" * 32)
            with pytest.raises(JobError) as mismatch:
                await finish_dataset(session, claim, wrong, archive=archive)
            assert mismatch.value.code == "job_result_mismatch"
            ref = await archive.build_dataset(claim.parameters, idempotency_key=claim.job_id, request_id=claim.request_id)
            assert await finish_dataset(session, claim, ref, archive=archive)
            assert await release_claim(session, claim)
            await session.commit()
            result = await queue.get(claim.job_id)
            assert result["status"] == "completed" and result["result"] == ref.model_dump(mode="json")
            assert result["attempts"] == 1 and result["workflow_version"] == "stock-v2-dataset-job-v1"
            with pytest.raises(JobError) as finished:
                await queue.cancel(claim.job_id)
            assert finished.value.status == 409
            assert (await queue.submit(command, request_id="d" * 32))["id"] == str(claim.job_id)


@pytest.mark.asyncio
async def test_expired_worker_fences_old_claim_never_auto_requeues_and_receipt_survives_unavailability(tmp_path, monkeypatch):
    from app.services.stock_research_jobs import utc_now
    from app.services.stock_research_v2 import jobs
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = jobs.JobService(session, scope)
            base = utc_now()
            monkeypatch.setattr(jobs, "utc_now", lambda: base)
            lease = await jobs.acquire_worker(session, uuid4())
            created = await queue.submit(command, request_id="b" * 32)
            claimed = await jobs.claim_next(session, lease)
            monkeypatch.setattr(jobs, "utc_now", lambda: base + timedelta(seconds=31))
            assert await queue.available() is False
            assert not await jobs.renew_claim(session, claimed)
            assert (await queue.submit(command, request_id="c" * 32))["id"] == created["id"]
            replacement = await jobs.acquire_worker(session, uuid4())
            assert replacement.token != lease.token
            assert (await queue.get(claimed.job_id))["status"] == "expired"
            assert await jobs.claim_next(session, replacement) is None
            assert not await jobs.finish_dataset(session, claimed, None)
            assert not await jobs.release_claim(session, claimed)


@pytest.mark.asyncio
async def test_queue_backpressure_is_active_only_not_a_lifetime_usage_limit(tmp_path):
    from app.schemas.stock_research_v2_jobs import CreateDatasetJob
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = JobService(session, scope)
            await acquire_worker(session, uuid4())
            first = await queue.submit(command, request_id="b" * 32)
            second_command = CreateDatasetJob.model_validate({**command.model_dump(), "idempotency_key": uuid4()})
            second = await queue.submit(second_command, request_id="c" * 32)
            third_command = CreateDatasetJob.model_validate({**command.model_dump(), "idempotency_key": uuid4()})
            with pytest.raises(JobError) as full:
                await queue.submit(third_command, request_id="d" * 32)
            assert full.value.status == 429
            await queue.cancel(UUID(first["id"]))
            assert (await queue.submit(third_command, request_id="e" * 32))["status"] == "pending"


@pytest.mark.asyncio
async def test_completed_record_requires_nonnull_fixed_result_fields_in_the_database(tmp_path):
    from app.models.stock_research_v2_job import StockResearchV2Job
    from app.services.stock_research_jobs import utc_now
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            queue = JobService(session, scope)
            await acquire_worker(session, uuid4())
            created = await queue.submit(command, request_id="b" * 32)
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await archive.build_dataset(command.parameters, idempotency_key=uuid4(), request_id="c" * 32)
            await session.commit()
            with pytest.raises(IntegrityError):
                await session.execute(update(StockResearchV2Job).where(StockResearchV2Job.id == UUID(created["id"])).values(status="completed", finished_at=utc_now(), result_dataset_id=ref.dataset_id, result_data_version=None, result_qualification=None))
            await session.rollback()


@pytest.mark.asyncio
async def test_idle_heartbeat_failure_ack_and_input_hash_corruption_are_explicit(tmp_path, monkeypatch):
    from app.models.stock_research_v2_job import StockResearchV2Job
    from app.services.stock_research_jobs import utc_now
    from app.services.stock_research_v2 import jobs
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        async with factory() as session:
            base = utc_now()
            monkeypatch.setattr(jobs, "utc_now", lambda: base)
            lease = await jobs.acquire_worker(session, uuid4())
            monkeypatch.setattr(jobs, "utc_now", lambda: base + timedelta(seconds=10))
            assert await jobs.heartbeat_worker(session, lease)
            monkeypatch.setattr(jobs, "utc_now", lambda: base + timedelta(seconds=20))
            queue = jobs.JobService(session, scope)
            created = await queue.submit(command, request_id="b" * 32)
            claimed = await jobs.claim_next(session, lease)
            assert not await jobs.heartbeat_worker(session, lease)
            assert await jobs.fail_claim(session, claimed, "worker_failed")
            assert await jobs.release_claim(session, claimed)
            assert (await queue.get(claimed.job_id))["error_code"] == "worker_failed"
            another = command.model_copy(update={"idempotency_key": uuid4()})
            waiting = await queue.submit(another, request_id="c" * 32)
            await session.execute(update(StockResearchV2Job).where(StockResearchV2Job.id == UUID(waiting["id"])).values(request_hash="0" * 64))
            await session.commit()
            assert await jobs.claim_next(session, lease) is None
            assert (await queue.get(UUID(waiting["id"])))["error_code"] == "job_input_integrity_failed"
