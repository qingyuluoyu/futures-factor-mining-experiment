import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError


def process(code):
    return subprocess.Popen([sys.executable, "-u", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


async def alive():
    return True


@pytest.mark.asyncio
async def test_observation_result_is_strictly_typed_and_child_is_stopped_before_return():
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    reference = {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only"}
    child = process(f"import json,time; print(json.dumps({{'type':'result','result':{reference!r}}}),flush=True); time.sleep(30)")
    result = await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=5)
    assert result.model_dump(mode="json") == reference
    assert child.poll() is not None and child.stdout.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"dataset_id": "not-uuid", "data_version": "a" * 64, "qualification": "observation_only"}, {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "backtest_ready"}, {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only", "owner_id": str(uuid4())}])
async def test_unverified_result_or_extra_control_fields_cannot_become_dataset_receipt(value):
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process(f"import json; print(json.dumps({{'type':'result','result':{value!r}}}),flush=True)")
    with pytest.raises(JobError) as failed:
        await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=5)
    assert failed.value.code == "dataset_invalid"
    assert child.poll() is not None


@pytest.mark.asyncio
async def test_lost_lease_cancels_child_without_a_result_or_auto_restart():
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process("import time; time.sleep(30)")

    async def lost():
        return False

    assert await supervise_dataset_process(child, heartbeat=lost, timeout_seconds=5) is None
    assert child.poll() is not None


@pytest.mark.asyncio
async def test_heartbeat_failure_does_not_expose_backend_details_and_reaps_child():
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process("import time; time.sleep(30)")

    async def failed():
        raise JobError("private_database_error", "secret-connection-details", 503)

    with pytest.raises(JobError) as failure:
        await supervise_dataset_process(child, heartbeat=failed, timeout_seconds=5)
    assert failure.value.code == "worker_failed"
    assert "secret-connection-details" not in str(failure.value)
    assert child.poll() is not None


@pytest.mark.asyncio
async def test_timeout_and_parent_cancellation_reap_the_real_child():
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process("import time; time.sleep(30)")
    with pytest.raises(JobError) as expired:
        await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=0.1)
    assert expired.value.code == "execution_timeout" and expired.value.status == 504
    assert child.poll() is not None
    child = process("import time; time.sleep(30)")
    task = asyncio.create_task(supervise_dataset_process(child, heartbeat=alive, timeout_seconds=5))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.poll() is not None and child.stdout.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["not-json", "x" * 5000, json.dumps({"type": "error", "code": "provider_secret", "status": 503, "message": "private-secret-token"}), json.dumps({"type": "snapshot", "manifest": {"untrusted": True}})])
async def test_protocol_violations_and_provider_text_are_sanitized_and_do_not_leave_children(output):
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process(f"print({output!r},flush=True)")
    with pytest.raises(JobError) as failed:
        await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=5)
    assert failed.value.code in {"dataset_invalid", "worker_failed"}
    assert "private-secret-token" not in str(failed.value)
    assert child.poll() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [True, float("inf"), float("nan"), 301, 0])
async def test_invalid_resource_budgets_are_rejected_and_owned_child_is_reaped(timeout):
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process("import time; time.sleep(30)")
    with pytest.raises(JobError) as failed:
        await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=timeout)
    assert failed.value.code == "invalid_execution_budget"
    assert child.poll() is not None
