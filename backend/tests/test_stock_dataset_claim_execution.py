from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_jobs import setup


async def claimed(factory, scope, command):
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next
    async with factory() as session:
        lease = await acquire_worker(session, uuid4())
        await JobService(session, scope).submit(command, request_id="b" * 32)
        result = await claim_next(session, lease)
        await session.commit()
        return result


@pytest.mark.asyncio
async def test_claim_builds_real_fixed_archive_idempotently_but_does_not_complete_job(tmp_path):
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        reference = await build_claim_dataset(factory, ArtifactStore(root), claim)
        assert reference.qualification == "observation_only"
        assert await build_claim_dataset(factory, ArtifactStore(root), claim) == reference
        async with factory() as session:
            archive = service(session, owner, root)
            manifest = await archive.read_dataset(reference)
            assert manifest["pool_version"] == command.parameters.pool_version
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == "running" and task["result"] is None


@pytest.mark.asyncio
async def test_cancelled_or_forged_claim_never_writes_a_dataset(tmp_path):
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        before = sorted(path.name for path in root.iterdir())
        assert await build_claim_dataset(factory, ArtifactStore(root), claim.model_copy(update={"token": uuid4()})) is None
        async with factory() as session:
            await JobService(session, scope).cancel(claim.job_id)
            await session.commit()
        assert await build_claim_dataset(factory, ArtifactStore(root), claim) is None
        assert sorted(path.name for path in root.iterdir()) == before
        async with factory() as session:
            assert (await service(session, owner, root).list_datasets(limit=20, offset=0))["total"] == 0


@pytest.mark.asyncio
async def test_corrupt_source_fails_without_publishing_dataset_or_marking_job_completed(tmp_path):
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        async with factory() as session:
            rows = await service(session, owner, root).source_rows(command.parameters.source_ids)
            source, artifact = rows[0]
            (root / artifact.relative_path).write_text("corrupted-qa-file", encoding="utf8")
        with pytest.raises(JobError):
            await build_claim_dataset(factory, ArtifactStore(root), claim)
        async with factory() as session:
            assert (await service(session, owner, root).list_datasets(limit=20, offset=0))["total"] == 0
            assert (await JobService(session, scope).get(claim.job_id))["status"] == "running"


@pytest.mark.asyncio
async def test_late_lease_loss_rolls_back_registered_dataset_without_deleting_shared_files(tmp_path, monkeypatch):
    from app.services.stock_research_v2 import claim_execution
    from app.services.stock_research_v2.artifacts import ArtifactStore
    original = claim_execution.fenced
    calls = 0

    async def lost_after_build(session, claim):
        nonlocal calls
        calls += 1
        return await original(session, claim) if calls == 1 else None

    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        monkeypatch.setattr(claim_execution, "fenced", lost_after_build)
        assert await claim_execution.build_claim_dataset(factory, ArtifactStore(root), claim) is None
        assert calls == 2
        async with factory() as session:
            archive = service(session, owner, root)
            assert (await archive.list_datasets(limit=20, offset=0))["total"] == 0
            assert (await archive.read_source(command.parameters.source_ids[0])).observations[0].values["close"] == "12.000000000000000001"


@pytest.mark.asyncio
async def test_commit_failure_is_not_returned_as_a_persisted_result(tmp_path):
    from contextlib import asynccontextmanager
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.artifacts import ArtifactStore
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)

        @asynccontextmanager
        async def failing_factory():
            async with factory() as session:
                async def fail_commit():
                    raise RuntimeError("qa-commit-failed")
                session.commit = fail_commit
                yield session

        with pytest.raises(RuntimeError, match="qa-commit-failed"):
            await build_claim_dataset(failing_factory, ArtifactStore(root), claim)
        async with factory() as session:
            assert (await service(session, owner, root).list_datasets(limit=20, offset=0))["total"] == 0
