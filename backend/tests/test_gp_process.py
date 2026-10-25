from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys

import pytest

from app.services.factor_lab.search import search_stepwise
from app.services.factor_mining.process_worker import ROOT, execute_generation
from app.services.stock_research_worker import JobStopped, ProcessExecutionError, supervise_process
from tests.test_gp_checkpoint import config
from tests.test_gp_checkpoint_store import frozen_input
from dataclasses import asdict


@pytest.mark.asyncio
async def test_real_subprocess_resumes_same_checkpoint_without_database_or_provider():
    payload, cfg = frozen_input(), config(generations=3)
    expected = list(search_stepwise(payload["bars"], "1d", cfg))
    checkpoint = None
    for index, snapshot in enumerate(expected):
        async def heartbeat(*args):
            return True
        result = await execute_generation({"input": payload, "config": asdict(cfg), "checkpoint": checkpoint, "generation": index,
            "deadline_at": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}, heartbeat=heartbeat)
        assert result["generation"] == index + 1
        assert result["checkpoint"] == snapshot.checkpoint
        checkpoint = result["checkpoint"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [True, False])
async def test_actual_sleeping_process_is_killed_and_reaped_on_cancel_or_timeout(cancelled):
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    async def heartbeat(*args):
        return not cancelled
    with pytest.raises(JobStopped if cancelled else ProcessExecutionError):
        await supervise_process(process, heartbeat=heartbeat, snapshot=heartbeat, timeout_seconds=.2,
            max_message_bytes=1024, timeout_message="GP测试超时；已终止")
    assert process.poll() is not None and process.stdout.closed and process.stdin.closed


def test_child_watchdog_obeys_absolute_deadline_before_stdin_is_received():
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    process = subprocess.Popen([sys.executable, "-u", "-m", "app.services.gp_generation_process", deadline], cwd=str(ROOT),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        assert process.wait(timeout=3) == 124
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
