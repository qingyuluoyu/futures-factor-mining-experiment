import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.services.stock_research_jobs import JobError
from tests.test_stock_columnar_registry import prepared
from tests.test_stock_dataset_claim_execution import claimed
from tests.test_stock_research_archive_v2 import service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_jobs import setup


async def context(factory, owner, tmp_path, *, migrate=True):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.columnar_store import ParquetStore
    root = tmp_path / "artifacts"
    scope, command = await setup(factory, owner, root)
    if migrate:
        await prepared(factory, parent_ready=True)
    claim = await claimed(factory, scope, command)
    target = tmp_path / "columnar"; target.mkdir()
    return scope, claim, ArtifactStore(root), ParquetStore(target)


@pytest.mark.asyncio
async def test_claim_registers_parent_and_real_parquet_atomically_idempotently_without_completing_job(tmp_path):
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.jobs import JobService
    async with job_database() as (factory, owner, other):
        scope, claim, source, parquet = await context(factory, owner, tmp_path)
        receipt = await build_claim_dataset(factory, source, claim, columnar_store=parquet)
        assert receipt.dataset.qualification == "observation_only"
        assert await build_claim_dataset(factory, source, claim, columnar_store=parquet) == receipt
        async with factory() as session:
            view = await ColumnarService(service(session, owner, source.root), parquet).read(receipt, expected_idempotency_key=claim.job_id)
            batch = next(view.batches(fields=["close", "volume"]))
            assert batch["rows"][0]["volume"] == "0.01" and batch["metadata"]["units"]["volume"] == "share"
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == "running" and task["result"] is None


@pytest.mark.asyncio
async def test_unmigrated_columnar_claim_does_not_write_either_parent_or_parquet(tmp_path):
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    async with job_database() as (factory, owner, other):
        _, claim, source, parquet = await context(factory, owner, tmp_path, migrate=False)
        before = {path.name: path.read_bytes() for path in source.root.iterdir()}
        with pytest.raises(JobError) as unavailable:
            await build_claim_dataset(factory, source, claim, columnar_store=parquet)
        assert unavailable.value.code == "columnar_schema_unavailable"
        assert {path.name: path.read_bytes() for path in source.root.iterdir()} == before
        assert not list(parquet.root.iterdir())


@pytest.mark.asyncio
async def test_late_lease_loss_rolls_back_both_registries_without_destroying_shared_source(tmp_path, monkeypatch):
    from app.models.stock_columnar import StockColumnarDataset
    from app.services.stock_research_v2 import claim_execution
    original = claim_execution.fenced
    calls = 0
    async def expire_after_build(session, claim):
        nonlocal calls
        calls += 1
        return await original(session, claim) if calls == 1 else None
    async with job_database() as (factory, owner, other):
        _, claim, source, parquet = await context(factory, owner, tmp_path)
        monkeypatch.setattr(claim_execution, "fenced", expire_after_build)
        assert await claim_execution.build_claim_dataset(factory, source, claim, columnar_store=parquet) is None
        assert calls == 2
        async with factory() as session:
            archive = service(session, owner, source.root)
            assert (await archive.list_datasets(limit=20, offset=0))["total"] == 0
            assert await session.scalar(select(func.count()).select_from(StockColumnarDataset)) == 0
            assert (await archive.read_source(claim.parameters.source_ids[0])).observations[0].values["volume"] == "0.01"


@pytest.mark.asyncio
async def test_columnar_ipc_parent_verifies_receipt_then_publishes_compatible_dataset_result(tmp_path):
    from app.models.stock_columnar import StockColumnarDataset
    from app.services.stock_research_v2.dataset_process import execute_payload
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.jobs import JobService, worker_row
    async with job_database() as (factory, owner, other):
        scope, claim, source, parquet = await context(factory, owner, tmp_path)
        async def local_child(value, store, heartbeat):
            assert await heartbeat()
            return await execute_payload(value.model_dump_json().encode(), store.root, session_factory=factory, columnar_root=parquet.root)
        assert await execute_claim(factory, source, claim, runner=local_child, columnar_store=parquet) is True
        async with factory() as session:
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == "completed" and task["result"]["qualification"] == "observation_only"
            row = await session.scalar(select(StockColumnarDataset).where(StockColumnarDataset.idempotency_key == claim.job_id))
            assert row.dataset_id == UUID(task["result"]["dataset_id"])
            assert (await worker_row(session)).busy_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["cancelled", "wrong_key", "corrupted"])
async def test_columnar_parent_rejects_cancelled_wrong_job_or_corrupted_receipt_and_releases_slot(tmp_path, bad):
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.jobs import JobService, worker_row
    async with job_database() as (factory, owner, other):
        scope, claim, source, parquet = await context(factory, owner, tmp_path)
        async def child(value, store, heartbeat):
            receipt = await build_claim_dataset(factory, store, value, columnar_store=parquet)
            if bad == "cancelled":
                async with factory() as session:
                    await JobService(session, scope).cancel(value.job_id); await session.commit()
            elif bad == "wrong_key":
                async with factory() as session:
                    receipt = await ColumnarService(service(session, owner, source.root), parquet).build(receipt.dataset, idempotency_key=uuid4(), request_id=value.request_id)
                    await session.commit()
            else:
                next(parquet.root.glob("*.parquet")).write_bytes(b"corrupted-qa")
            return receipt
        assert await execute_claim(factory, source, claim, runner=child, columnar_store=parquet) is False
        async with factory() as session:
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == ("cancelled" if bad == "cancelled" else "failed") and task["result"] is None
            assert (await worker_row(session)).busy_job_id is None


@pytest.mark.asyncio
async def test_columnar_launcher_sets_bounded_native_threads_without_changing_json_mode(tmp_path, monkeypatch):
    from app.services.stock_research_v2 import dataset_worker
    observed = []
    class LaunchCaptured(Exception):
        pass
    def capture(command, *, cwd, env):
        observed.append((command, env))
        raise LaunchCaptured()
    async with job_database() as (factory, owner, other):
        _, claim, source, parquet = await context(factory, owner, tmp_path)
        monkeypatch.setattr(dataset_worker, "spawn_bounded_process", capture)
        monkeypatch.setenv("OPENBLAS_NUM_THREADS", "9")
        async def alive(): return True
        with pytest.raises(LaunchCaptured):
            await dataset_worker.run_claim_process(claim, source, alive, columnar_store=parquet)
        command, environment = observed[-1]
        assert command[-1] == "app.services.stock_research_v2.columnar_process"
        assert environment["WENYUAN_STOCK_COLUMNAR_ROOT"] == str(parquet.root)
        for field in ["OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
            assert environment[field] == "1"
        with pytest.raises(LaunchCaptured):
            await dataset_worker.run_claim_process(claim, source, alive)
        assert observed[-1][0][-1] == "app.services.stock_research_v2.dataset_process"
        assert observed[-1][1]["OPENBLAS_NUM_THREADS"] == "9"


@pytest.mark.asyncio
async def test_columnar_one_shot_does_not_claim_queue_when_018_is_missing(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.columnar_store import ParquetStore
    from app.services.stock_research_v2.dataset_worker import run_dataset_once
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, worker_row
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        target = tmp_path / "columnar"; target.mkdir()
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            created = await JobService(session, scope).submit(command, request_id="b" * 32)
            await session.commit()
        with pytest.raises(JobError) as unavailable:
            await run_dataset_once(factory, ArtifactStore(root), lease, columnar_store=ParquetStore(target))
        assert unavailable.value.code == "columnar_schema_unavailable"
        async with factory() as session:
            task = await JobService(session, scope).get(UUID(created["id"]))
            assert task["status"] == "pending" and task["attempts"] == 0
            assert (await worker_row(session)).busy_job_id is None


@pytest.mark.asyncio
async def test_explicit_columnar_one_shot_consumes_and_releases_one_existing_job_without_starting_a_loop(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.columnar_store import ParquetStore
    from app.services.stock_research_v2.dataset_process import execute_payload
    from app.services.stock_research_v2.dataset_worker import run_dataset_once
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, worker_row
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        await prepared(factory, parent_ready=True)
        target = tmp_path / "columnar"; target.mkdir()
        parquet = ParquetStore(target)
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            created = await JobService(session, scope).submit(command, request_id="b" * 32)
            await session.commit()
        async def child(value, store, heartbeat):
            assert await heartbeat()
            return await execute_payload(value.model_dump_json().encode(), store.root, session_factory=factory, columnar_root=target)
        assert await run_dataset_once(factory, ArtifactStore(root), lease, runner=child, columnar_store=parquet) is True
        assert await run_dataset_once(factory, ArtifactStore(root), lease, runner=child, columnar_store=parquet) is False
        async with factory() as session:
            assert (await JobService(session, scope).get(UUID(created["id"])))["status"] == "completed"
            assert (await worker_row(session)).busy_job_id is None


@pytest.mark.asyncio
async def test_default_columnar_parent_selects_its_explicit_columnar_launcher(tmp_path, monkeypatch):
    from app.services.stock_research_v2 import dataset_worker
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    received = []
    async with job_database() as (factory, owner, other):
        _, claim, source, parquet = await context(factory, owner, tmp_path)
        async def launcher(value, store, heartbeat, *, columnar_store=None):
            received.append(columnar_store)
            return await build_claim_dataset(factory, store, value, columnar_store=columnar_store)
        def forbidden(*args, **kwargs):
            pytest.fail("orchestration unit test must not connect a real child to stopped PG")
        monkeypatch.setattr(dataset_worker, "run_claim_process", launcher)
        monkeypatch.setattr(dataset_worker, "spawn_bounded_process", forbidden)
        assert await dataset_worker.execute_claim(factory, source, claim, columnar_store=parquet) is True
        assert received == [parquet]


@pytest.mark.asyncio
async def test_columnar_commit_failure_returns_no_receipt_and_rolls_back_both_registries(tmp_path):
    from contextlib import asynccontextmanager
    from app.models.stock_columnar import StockColumnarDataset
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    async with job_database() as (factory, owner, other):
        _, claim, source, parquet = await context(factory, owner, tmp_path)
        @asynccontextmanager
        async def broken_factory():
            async with factory() as session:
                async def fail():
                    raise RuntimeError("qa commit failed")
                session.commit = fail
                yield session
        with pytest.raises(RuntimeError, match="qa commit failed"):
            await build_claim_dataset(broken_factory, source, claim, columnar_store=parquet)
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(StockColumnarDataset)) == 0
            assert (await service(session, owner, source.root).list_datasets(limit=20, offset=0))["total"] == 0


@pytest.mark.asyncio
async def test_invalid_columnar_root_is_rejected_before_a_session_opens(tmp_path):
    from app.services.stock_research_v2.dataset_process import execute_payload
    async with job_database() as (factory, owner, other):
        _, claim, source, _ = await context(factory, owner, tmp_path)
        def forbidden():
            pytest.fail("invalid roots must not open a session")
        with pytest.raises(JobError):
            await execute_payload(claim.model_dump_json().encode(), source.root, session_factory=forbidden, columnar_root=tmp_path / "missing")
        assert not (tmp_path / "missing").exists()


def test_real_columnar_cli_fails_closed_without_roots_or_database_access(tmp_path):
    env = {**os.environ, "WENYUAN_STOCK_DATASET_ROOT": str(tmp_path)}
    env.pop("WENYUAN_STOCK_COLUMNAR_ROOT", None)
    child = subprocess.run([sys.executable, "-u", "-m", "app.services.stock_research_v2.columnar_process"],
        cwd=Path(__file__).resolve().parents[1], env=env, input=b"{}\n", capture_output=True, timeout=15)
    assert child.returncode == 1
    response = json.loads(child.stdout)
    assert response["type"] == "error" and response["code"] == "dataset_invalid"
    assert "Traceback" not in child.stderr.decode(errors="replace")
