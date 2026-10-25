import asyncio
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.stock_research_worker import JobStopped, ProcessExecutionError, supervise_process


def process(code):
    return subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


@pytest.mark.asyncio
async def test_supervisor_terminates_real_slow_process_at_deadline():
    child = process("import time; time.sleep(60)")

    async def heartbeat():
        return True

    started = time.monotonic()
    with pytest.raises(ProcessExecutionError) as expired:
        await supervise_process(child, heartbeat=heartbeat, snapshot=lambda value: None, timeout_seconds=0.15)
    assert expired.value.code == "execution_timeout" and expired.value.terminal_status == "expired"
    assert time.monotonic() - started < 6 and child.poll() is not None


@pytest.mark.asyncio
async def test_database_heartbeat_wait_does_not_escape_the_execution_deadline():
    child = process("import time; time.sleep(60)")

    async def slow_heartbeat():
        await asyncio.sleep(1)
        return True

    started = time.monotonic()
    with pytest.raises(ProcessExecutionError):
        await supervise_process(child, heartbeat=slow_heartbeat, snapshot=lambda value: None, timeout_seconds=0.1)
    assert time.monotonic() - started < 0.7 and child.poll() is not None


@pytest.mark.asyncio
async def test_supervisor_cancellation_kills_the_process_not_just_a_future():
    child = process("import time; time.sleep(60)")

    async def cancelled():
        return False

    with pytest.raises(JobStopped):
        await supervise_process(child, heartbeat=cancelled, snapshot=lambda value: None, timeout_seconds=10)
    assert child.poll() is not None


@pytest.mark.asyncio
async def test_supervisor_checks_protocol_and_never_returns_arbitrary_stdout_as_error_detail():
    child = process("print('private-error-do-not-publish',flush=True)")

    async def heartbeat():
        return True

    with pytest.raises(ProcessExecutionError) as malformed:
        await supervise_process(child, heartbeat=heartbeat, snapshot=lambda value: None, timeout_seconds=5)
    assert malformed.value.code == "worker_protocol"
    assert "private-error" not in str(malformed.value)


@pytest.mark.asyncio
async def test_supervisor_streams_a_snapshot_and_returns_a_real_process_result():
    child = process("import json; print(json.dumps({'type':'snapshot','manifest':{'data_version':'a'*64}}),flush=True); print(json.dumps({'type':'result','result':{'validation_status':'research_only'}}),flush=True)")
    observed = []

    async def heartbeat():
        return True

    async def snapshot(value):
        observed.append(value)
        return True

    result = await supervise_process(child, heartbeat=heartbeat, snapshot=snapshot, timeout_seconds=5)
    assert result["validation_status"] == "research_only"
    assert observed == [{"data_version": "a" * 64}]
    assert child.poll() is not None


@pytest.mark.asyncio
async def test_snapshot_database_wait_cannot_escape_the_execution_deadline():
    child = process("import json,time; print(json.dumps({'type':'snapshot','manifest':{}}),flush=True); time.sleep(60)")

    async def heartbeat():
        return True

    async def slow_snapshot(value):
        await asyncio.sleep(5)
        return True

    started = time.monotonic()
    with pytest.raises(ProcessExecutionError) as failure:
        await supervise_process(child, heartbeat=heartbeat, snapshot=slow_snapshot, timeout_seconds=0.2)
    assert failure.value.code == "execution_timeout"
    assert time.monotonic() - started < 1 and child.poll() is not None


def test_orphan_process_refuses_a_job_past_its_absolute_hard_deadline():
    import json

    child = subprocess.run(
        [sys.executable, "-u", "-m", "app.services.stock_research_process"],
        cwd=Path(__file__).resolve().parents[1],
        input=json.dumps({"kind": "analysis", "parameters": {}, "deadline_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()}).encode() + b"\n",
        capture_output=True, timeout=5,
    )
    assert child.returncode == 124 and child.stdout == b""


@pytest.mark.asyncio
async def test_shutdown_database_failure_never_overrides_task_cancellation(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import app.services.stock_research_worker as module

    claimed = SimpleNamespace(id=uuid4(), worker_token=uuid4(), user_id=uuid4(), trading_mode="virtual", request_id="a" * 32, deadline_at=datetime.now(timezone.utc) + timedelta(seconds=120), kind="analysis", params_json={})
    entered = asyncio.Event()
    sessions = 0

    class DatabaseSession:
        async def __aenter__(self):
            nonlocal sessions
            sessions += 1
            if sessions > 1:
                raise RuntimeError("database unavailable during shutdown")
            return self

        async def __aexit__(self, *args):
            pass

        async def commit(self):
            pass

    async def claim(session):
        return claimed

    async def supervise(child, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    original_popen = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = original_popen([sys.executable, "-u", "-c", "import time; time.sleep(60)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        children.append(child)
        return child

    monkeypatch.setattr(module, "claim_next_job", claim)
    monkeypatch.setattr(module, "supervise_process", supervise)
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    worker = module.StockResearchWorker(DatabaseSession)
    worker.start()
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(worker.stop(), 2)
    assert worker.task is None and sessions == 3 and all(child.poll() is not None for child in children)
