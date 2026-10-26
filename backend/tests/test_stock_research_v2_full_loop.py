"""The whole dispatched-research loop, with a real subprocess and a real publication.

The ④-3 tests check the parent's protocol against a fake spawn and the child's kernel
in-process, and the IPC tests check the child alone.  Nothing yet proves the three
joined: a submitted research job, a real lease, the real bounded subprocess, and the
parent's own re-derivation of the artifact row and the job result from the fenced
claim.  That is the only path that actually ships, so it gets its own end-to-end test.
"""
from uuid import UUID, uuid4

import pytest

from tests.test_stock_benchmark_v2 import index_series, portfolio
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_task_dispatch import (CURRENT_DATASET, fixed_dataset, kind_parameters, prepare_registry,
    register_chain)


async def prepared(tmp_path, factory, owner):
    """A 021-registered engine with one fixed, ready dataset and a live worker lease."""
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import acquire_worker
    await prepare_registry(factory)
    await register_chain(factory)
    CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
    async with factory() as session:
        lease = await acquire_worker(session, uuid4())
        await session.commit()
    # The store is created before submission: a benchmark command's strategy digest is
    # part of its request hash, so the artifact cannot be swapped in afterwards.
    return ArtifactStore(tmp_path / "artifacts"), lease


@pytest.mark.asyncio
async def test_a_claimed_benchmark_job_completes_through_a_real_subprocess(tmp_path):
    from app.schemas.stock_research_v2 import Scope
    from app.schemas.stock_research_v2_jobs import CreateBenchmarkJob
    from app.services.stock_research_v2 import task_execution
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.jobs import JobService, claim_next
    async with job_database() as (factory, owner, other):
        store, lease = await prepared(tmp_path, factory, owner)
        stored = store.put_json(portfolio().model_dump(mode="json"))
        scope = Scope(owner_id=owner, trading_mode="virtual")
        command = CreateBenchmarkJob(idempotency_key=uuid4(), parameters={"kind": "benchmark",
            **kind_parameters("benchmark"), "strategy_artifact_sha256": stored.sha256})
        async with factory() as session:
            submitted = await JobService(session, scope).submit(command, request_id="c" * 32)
            claim = await claim_next(session, lease)
            assert claim is not None and claim.kind == "benchmark"
            await session.commit()
        # The real runner: the claim is executed by its own bounded subprocess, and the
        # parent heartbeats the lease exactly as the production worker does.
        written = await execute_claim(factory, store, claim, runner=task_execution.run_research_process)
        assert written is True, "a completed kernel must publish exactly one result"
        async with factory() as session:
            finished = await JobService(session, scope).get(UUID(submitted["id"]))
        # The parent re-derived the artifact row and the result from the fenced claim;
        # the child never named any of them.
        assert finished["status"] == "completed" and finished["step"] == "completed"
        report_sha256 = finished["result"]["report_sha256"]
        report = store.read_json(report_sha256)
        assert report["validation_status"] == "qa_only"
        assert report["benchmark_metrics"]["periods_per_year"] == 244
        # The publication is registered under the claim's own scope and job key: the
        # fixed-dataset fixture's own artifact row is there too, so match on content.
        async with factory() as session:
            from app.models.stock_research_dataset import StockResearchArtifact
            from sqlalchemy import select
            rows = (await session.execute(select(StockResearchArtifact).where(StockResearchArtifact.owner_id == owner))).scalars().all()
            published = [row for row in rows if row.sha256 == report_sha256]
            assert len(published) == 1
            assert published[0].idempotency_key == claim.job_id
            assert published[0].purpose == "dataset" and published[0].state == "ready"


@pytest.mark.asyncio
async def test_a_kernel_whose_evidence_the_claim_does_not_carry_fails_the_job_loudly(tmp_path):
    from app.schemas.stock_research_v2 import Scope
    from app.schemas.stock_research_v2_jobs import CreateResearchICJob
    from app.services.stock_research_v2 import task_execution
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.jobs import JobService, claim_next
    async with job_database() as (factory, owner, other):
        store, lease = await prepared(tmp_path, factory, owner)
        scope = Scope(owner_id=owner, trading_mode="virtual")
        command = CreateResearchICJob(idempotency_key=uuid4(),
            parameters={"kind": "research_ic", **kind_parameters("research_ic")})
        async with factory() as session:
            submitted = await JobService(session, scope).submit(command, request_id="d" * 32)
            claim = await claim_next(session, lease)
            assert claim is not None and claim.kind == "research_ic"
            await session.commit()
        written = await execute_claim(factory, store, claim, runner=task_execution.run_research_process)
        assert written is False
        async with factory() as session:
            failed = await JobService(session, scope).get(UUID(submitted["id"]))
        assert failed["status"] == "failed" and failed["error_code"] == "task_inputs_unavailable"
        # A refused kernel names the gap instead of publishing a plausible report:
        # no result reference, no artifact row, and no content written anywhere.
        assert failed["result"] is None
        assert list(store.root.iterdir()) == []
