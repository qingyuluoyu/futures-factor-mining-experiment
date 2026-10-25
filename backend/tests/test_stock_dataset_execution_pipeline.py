import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_dataset_claim_execution import claimed
from tests.test_stock_research_archive_v2 import service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_jobs import setup


@pytest.mark.asyncio
async def test_ipc_handler_builds_actual_claim_and_parent_publishes_verified_result(tmp_path):
    from app.services.stock_research_v2.dataset_process import execute_payload
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService, worker_row
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)

        async def local_child(value, store, heartbeat):
            assert await heartbeat() is True
            return await execute_payload(value.model_dump_json().encode(), root, session_factory=factory)

        assert await execute_claim(factory, ArtifactStore(root), claim, runner=local_child) is True
        async with factory() as session:
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == "completed" and task["result"]["qualification"] == "observation_only"
            assert (await worker_row(session)).busy_job_id is None
            assert (await service(session, owner, root).list_datasets(limit=20, offset=0))["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"{}", b"{}\n{}", b"x" * (512 * 1024 + 1), b'{"job_id":1,"job_id":2}'], ids=["empty", "multiple", "oversize", "duplicate"])
async def test_bad_ipc_input_cannot_open_a_session_or_create_storage(tmp_path, raw):
    from app.services.stock_research_v2.dataset_process import execute_payload
    def forbidden():
        pytest.fail("bad IPC must fail before database access")
    with pytest.raises((JobError, ValueError)):
        await execute_payload(raw, tmp_path / "missing", session_factory=forbidden)
    assert not (tmp_path / "missing").exists()


@pytest.mark.asyncio
async def test_parent_cancellation_cannot_publish_late_result(tmp_path):
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService, worker_row
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        async def cancelled(value, store, heartbeat):
            result = await build_claim_dataset(factory, store, value)
            async with factory() as session:
                await JobService(session, scope).cancel(value.job_id)
                await session.commit()
            return result
        assert await execute_claim(factory, ArtifactStore(root), claim, runner=cancelled) is False
        async with factory() as session:
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == "cancelled" and task["result"] is None
            assert (await worker_row(session)).busy_job_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_result", "exception", "cancelled"])
async def test_parent_failure_is_terminal_sanitized_and_releases_slot(tmp_path, failure):
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService, worker_row
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        async def bad(value, store, heartbeat):
            if failure == "cancelled":
                raise asyncio.CancelledError()
            if failure == "exception":
                raise RuntimeError("private-connection-secret")
            return {"dataset_id": "bad", "data_version": "a" * 64, "qualification": "backtest_ready"}
        if failure == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await execute_claim(factory, ArtifactStore(root), claim, runner=bad)
        else:
            assert await execute_claim(factory, ArtifactStore(root), claim, runner=bad) is False
        async with factory() as session:
            task = await JobService(session, scope).get(claim.job_id)
            assert task["status"] == "failed" and task["result"] is None
            assert "private-connection-secret" not in json.dumps(task)
            assert (await worker_row(session)).busy_job_id is None


def test_real_cli_rejects_invalid_input_without_project_services_or_traceback(tmp_path):
    env = {**os.environ, "WENYUAN_STOCK_DATASET_ROOT": str(tmp_path / "missing")}
    result = subprocess.run([sys.executable, "-u", "-m", "app.services.stock_research_v2.dataset_process"],
        cwd=Path(__file__).resolve().parents[1], env=env, input=b"{}\n", capture_output=True, timeout=15)
    assert result.returncode == 1
    message = json.loads(result.stdout)
    assert message["type"] == "error" and message["code"] == "dataset_invalid"
    assert "Traceback" not in result.stderr.decode(errors="replace")
    assert not (tmp_path / "missing").exists()


@pytest.mark.asyncio
async def test_one_shot_renews_claims_and_finishes_existing_queue_without_starting_daemon(tmp_path):
    from app.services.stock_research_v2.dataset_worker import run_dataset_once
    from app.services.stock_research_v2.claim_execution import build_claim_dataset
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import JobService, acquire_worker
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            task = await JobService(session, scope).submit(command, request_id="a" * 32)
            await session.commit()
        async def local_child(value, store, heartbeat):
            return await build_claim_dataset(factory, store, value)
        assert await run_dataset_once(factory, ArtifactStore(root), lease, runner=local_child) is True
        assert await run_dataset_once(factory, ArtifactStore(root), lease, runner=local_child) is False
        async with factory() as session:
            assert (await JobService(session, scope).get(UUID(task["id"])))["status"] == "completed"


@pytest.mark.asyncio
async def test_launcher_missing_root_is_reaped_and_reports_error_without_database_access(tmp_path):
    from app.services.stock_research_v2.dataset_worker import run_claim_process
    from app.services.stock_research_v2.artifacts import ArtifactStore
    async with job_database() as (factory, owner, other):
        root = tmp_path / "artifacts"
        scope, command = await setup(factory, owner, root)
        claim = await claimed(factory, scope, command)
        removed = tmp_path / "removed"
        store = ArtifactStore(removed)
        removed.rmdir()
        async def alive():
            return True
        with pytest.raises(JobError) as error:
            await run_claim_process(claim, store, alive)
        assert error.value.code in {"dataset_invalid", "worker_failed"}
        assert not removed.exists()
