"""Parent-side execution of a dispatched research claim.

The claim's persisted ``kind`` selects the subprocess program; the program itself is
started through the same Windows job boundary the observation path uses
(``spawn_bounded_process``: job assigned atomically at creation, kill-on-close, 512MiB
committed memory, at most 4 processes), and it is supervised with the same lease
heartbeat, timeout and abnormal-termination reclamation.  Nothing here can be reached
with a kind that has no module, and the child's single message is validated against
``TaskOutcome`` and re-checked against the claim before the parent believes it.

The child never receives a database engine and never returns an identity: the parent
re-derives the experiment or artifact from the fenced claim, so a kernel cannot
publish a result, certify itself or initiate a trade.
"""
import asyncio
from functools import partial
import logging
import os
from pathlib import Path
import sys

from pydantic import ValidationError

from app.schemas.stock_research_v2_jobs import RESEARCH_MAX_RUNTIME, DatasetClaim
from app.services.stock_research_jobs import JobError, utc_now
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.task_dispatch import TASK_FAILURE_CODES, TaskOutcome, task_module, task_shape
from app.services.stock_research_v2.windows_job_process import spawn_bounded_process
from app.services.stock_research_worker import JobStopped, ProcessExecutionError, stop_process, supervise_process

logger = logging.getLogger("qihuo.stock_research_v2_task")
BACKEND_ROOT = Path(__file__).resolve().parents[3]
MAX_INPUT_BYTES = 512 * 1024
# Native BLAS pools otherwise initialize outside the row-batch budget.
THREAD_PINS = {name: "1" for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}


def claim_runner(claim, *, columnar_store=None):
    """The callable that executes ``claim``; the observation path is unchanged."""
    if not isinstance(claim, DatasetClaim):
        raise JobError("dataset_invalid", "领取任务结构无效", 422)
    if claim.kind == "dataset":
        from app.services.stock_research_v2.dataset_worker import run_claim_process
        return partial(run_claim_process, columnar_store=columnar_store) if columnar_store is not None else run_claim_process
    return run_research_process


async def supervise_research_process(process, *, heartbeat, timeout_seconds: float) -> TaskOutcome | None:
    async def renew():
        try:
            return await heartbeat() is True
        except Exception:
            raise JobError("worker_failed", "研究执行器续租失败，未保存完成结果", 500) from None

    async def reject_snapshot(manifest):
        raise JobError("task_kernel_failed", "研究执行器不接受额外控制消息", 422)

    try:
        payload = await supervise_process(process, heartbeat=renew, snapshot=reject_snapshot, timeout_seconds=timeout_seconds,
            max_message_bytes=4096, timeout_message="研究任务超时，子进程已终止，不自动重试")
        outcome = TaskOutcome.model_validate(payload)
    except JobStopped:
        return None
    except ProcessExecutionError as error:
        if error.code == "execution_timeout":
            raise JobError("execution_timeout", "研究任务超时，子进程已终止，不自动重试", 504) from None
        if error.code in {"worker_protocol", "result_too_large"}:
            raise JobError("task_kernel_failed", "研究执行器返回格式或大小无效", 422) from None
        # The child reports a *named* refusal precisely so the job row can record why
        # it stopped — ``task_inputs_unavailable`` (the calendar/universe/receipt a
        # kernel needs and the claim does not carry) is the ordinary outcome for four
        # of the five kinds.  Flattening it to ``worker_failed`` would throw away the
        # only diagnosis the operator gets, so a code the claim may legally persist is
        # carried through with the child's own status and message.  Anything else —
        # an unrecognised code, or free-form text a kernel or a model produced — is
        # still refused rather than persisted.
        if error.code in TASK_FAILURE_CODES and isinstance(error.status, int) and 400 <= error.status <= 599:
            raise JobError(error.code, error.message[:500], error.status) from None
        raise JobError("worker_failed", "研究执行器失败，未保存完成结果", 500) from None
    except ValidationError:
        raise JobError("task_kernel_failed", "研究结果未通过结构验证", 422) from None
    except JobError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise JobError("worker_failed", "研究执行器失败，未保存完成结果", 500) from None
    finally:
        await stop_process(process)
        if process.stdout:
            process.stdout.close()
        if process.stdin:
            process.stdin.close()
    return outcome


async def run_research_process(claim, store: ArtifactStore, heartbeat):
    """Run one dispatched claim in its own bounded subprocess and return its outcome."""
    claim = DatasetClaim.model_validate(claim.model_dump())
    if not isinstance(store, ArtifactStore) or not store.root.is_dir():
        raise JobError("artifact_store_unavailable", "研究产物存储根未就绪", 503)
    # Refuse before any process exists: a kind without a module is never spawned.
    module = task_module(claim.kind)
    shape = task_shape(claim.kind)
    raw = claim.model_dump_json().encode("utf8") + b"\n"
    remaining = (claim.deadline_at - utc_now()).total_seconds()
    if len(raw) > MAX_INPUT_BYTES or not 0 < remaining <= RESEARCH_MAX_RUNTIME:
        raise JobError("task_inputs_unavailable", "研究任务输入大小或期限无效", 422)
    environment = {**os.environ, "WENYUAN_STOCK_DATASET_ROOT": str(store.root), **THREAD_PINS}
    process = spawn_bounded_process([sys.executable, "-u", "-m", module], cwd=BACKEND_ROOT, env=environment)
    sender = None
    try:
        async def send():
            await asyncio.to_thread(process.stdin.write, raw)
            await asyncio.to_thread(process.stdin.flush)
            process.stdin.close()
            process.stdin = None
        # Start the input writer concurrently so a pipe that stalls cannot bypass supervision.
        sender = asyncio.create_task(send())
        outcome = await supervise_research_process(process, heartbeat=heartbeat, timeout_seconds=remaining)
    finally:
        try:
            await asyncio.to_thread(process.close)
        finally:
            if sender is not None:
                await asyncio.gather(sender, return_exceptions=True)
    if outcome is None:
        return None
    # The child reports content, never identity: re-check both against the claim.
    if outcome.kind != claim.kind or outcome.shape != shape:
        raise JobError("task_kernel_failed", "研究执行器返回的任务类型或结果形态与领取任务不一致", 422)
    logger.info("request_id=%s job_id=%s operation=research_kernel kind=%s shape=%s bytes=%s model_calls=0",
        claim.request_id, claim.job_id, outcome.kind, outcome.shape, outcome.report_byte_size)
    return outcome
