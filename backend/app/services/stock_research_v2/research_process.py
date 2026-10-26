"""Internal research-task IPC entry point; never migrates or starts a worker loop.

One bounded subprocess serves every dispatched research kind, exactly as
``dataset_process`` serves the observation kind.  It reads one ``DatasetClaim`` from
stdin, runs the kernel the claim's persisted ``kind`` selects, and reports a single
``TaskOutcome`` on stdout.  It has no database engine, no network client, no model
credential and no ability to name a job, an experiment or an artifact row: the
parent re-derives every identity from the fenced claim, so a kernel cannot publish
a result, certify itself or write anything.

The kernel table is closed.  A kind whose kernel has no registered implementation,
or whose required version-bound evidence is not carried by the claim, refuses with
a named ``error_code`` instead of producing a plausible-looking report.
"""
import asyncio
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading

from app.schemas.stock_research_v2_jobs import DatasetClaim
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.task_dispatch import TaskOutcome, task_module, task_shape

MAX_INPUT_BYTES = 512 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate IPC field")
        result[key] = value
    return result


def run_benchmark(claim, store):
    """Compare an already-stored strategy against the claim's own index series.

    Pure calculation over content-addressed inputs: no database session, no model
    call and no new external request, so it is safe to run inside the bounded child.
    """
    from app.services.stock_research_v2.artifacts import ArtifactError
    from app.services.stock_research_v2.benchmark import BenchmarkError, compare_to_index
    from app.services.stock_research_v2.comparison import save_comparison
    try:
        strategy = store.read_json(claim.parameters.strategy_artifact_sha256)
        report = compare_to_index(strategy, claim.parameters.index)
        return save_comparison(report, store)
    except (ArtifactError, BenchmarkError) as error:
        code = getattr(error, "code", "artifact_unavailable")
        raise JobError("task_inputs_unavailable", f"基准比较输入不完整或口径不一致：{code}", 422) from None


def run_unavailable(claim, store):
    """Refuse a kernel whose version-bound evidence the claim does not carry.

    ``research_ic`` / ``backtest`` / ``ml_train`` / ``report`` all need the
    point-in-time study inputs (trading calendar, historical universe and the
    columnar receipt) that ``load_study_inputs`` requires.  A ``DatasetRef`` alone
    is not that evidence, so these kernels stop here rather than fabricate a report
    from whatever data happens to be reachable.
    """
    raise JobError("task_inputs_unavailable", f"{claim.kind} 内核所需的日历/股票池/列式凭证未随任务提供；未运行、未产出报告", 422)


TASK_KERNELS = {"research_ic": run_unavailable, "backtest": run_unavailable, "benchmark": run_benchmark,
    "ml_train": run_unavailable, "report": run_unavailable}


def task_kernel(kind):
    if not isinstance(kind, str) or kind not in TASK_KERNELS:
        raise JobError("task_kind_unregistered", f"未登记的研究任务内核：{kind!r}", 422)
    return TASK_KERNELS[kind]


def run_task_kernel(claim: DatasetClaim, store: ArtifactStore) -> dict:
    """Run the claim's kernel and return the outcome it is allowed to report."""
    claim = DatasetClaim.model_validate(claim.model_dump())
    if not isinstance(store, ArtifactStore):
        raise JobError("artifact_store_unavailable", "研究产物存储根未就绪", 503)
    kernel = task_kernel(claim.kind)
    stored = kernel(claim, store)
    outcome = TaskOutcome(kind=claim.kind, shape=task_shape(claim.kind), report_sha256=stored.sha256, report_byte_size=stored.byte_size)
    return outcome.model_dump(mode="json")


async def execute_payload(raw: bytes, root: Path):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_INPUT_BYTES:
        raise ValueError("invalid IPC size")
    claim = DatasetClaim.model_validate(json.loads(raw, object_pairs_hook=unique_object))
    remaining = (claim.deadline_at - datetime.now(timezone.utc)).total_seconds()
    if not 0 < remaining <= 900:
        raise JobError("execution_timeout", "研究任务期限无效或已过期", 504)
    root = Path(root).absolute()
    if not root.is_dir() or root.resolve() != root:
        raise JobError("artifact_store_unavailable", "研究产物存储根未就绪", 503)
    store = ArtifactStore(root, max_bytes=MAX_REPORT_BYTES)
    # The claim must name a module this program can actually run.
    if task_module(claim.kind) != __name__:
        raise JobError("task_kind_unregistered", f"该任务类型不由此执行器运行：{claim.kind!r}", 422)
    return await asyncio.to_thread(run_task_kernel, claim, store)


def emit(payload):
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    if len(data) > 4096:
        raise ValueError("IPC response exceeded limit")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def main():
    watchdog = threading.Timer(905, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        raw = sys.stdin.buffer.readline(MAX_INPUT_BYTES + 1)
        configured = os.environ.get("WENYUAN_STOCK_DATASET_ROOT")
        if not configured:
            raise ValueError("research artifact root not configured")
        # Any incidental library output cannot become a trusted protocol message.
        with redirect_stdout(sys.stderr):
            outcome = asyncio.run(execute_payload(raw, Path(configured)))
        emit({"type": "result", "result": outcome})
        return 0
    except JobError as error:
        # JobError is a ValueError, so it must be reported before the generic branch.
        emit({"type": "error", "code": error.code, "status": error.status, "message": error.message[:500]})
        return 1
    except (ValueError, RecursionError):
        emit({"type": "error", "code": "task_inputs_unavailable", "status": 422, "message": "研究任务输入或产物校验失败"})
        return 1
    except Exception:
        emit({"type": "error", "code": "task_kernel_failed", "status": 500, "message": "研究内核执行失败，未发布结果"})
        return 1
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
