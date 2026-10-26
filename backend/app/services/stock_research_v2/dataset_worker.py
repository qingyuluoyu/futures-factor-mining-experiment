"""Explicit, one-claim orchestration. Not mounted in application lifecycle."""
import asyncio
import logging
import os
from pathlib import Path
import sys

from app.schemas.stock_research_v2_jobs import DatasetClaim, WorkerLease
from app.services.stock_research_jobs import JobError, utc_now
from app.services.stock_research_v2.archive import ArchiveService, descriptor
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.jobs import claim_next, fail_claim, finish_dataset, finish_report, heartbeat_worker, recover_expired, release_claim, renew_claim
from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
from app.services.stock_research_v2.task_dispatch import TASK_FAILURE_CODES
from app.services.stock_research_v2.task_execution import claim_runner, run_research_process
from app.services.stock_research_v2.windows_job_process import spawn_bounded_process

logger = logging.getLogger("qihuo.stock_dataset_worker")
BACKEND_ROOT = Path(__file__).resolve().parents[3]
__all__ = ["claim_runner", "execute_claim", "run_claim_process", "run_dataset_once", "run_research_process"]


async def run_dataset_once(session_factory, store: ArtifactStore, lease: WorkerLease, *, runner=None, columnar_store=None):
    """Use an explicitly acquired lease; never acquires or steals one implicitly."""
    lease = WorkerLease.model_validate(lease.model_dump())
    if not isinstance(store, ArtifactStore) or not store.root.is_dir():
        raise JobError("artifact_store_unavailable", "观察存储根未就绪", 503)
    async with asyncio.timeout(5), session_factory() as session:
        try:
            if columnar_store is not None:
                from app.services.stock_research_v2.columnar import columnar_ready
                from app.services.stock_research_v2.columnar_store import ParquetStore, arrow_engine
                if not isinstance(columnar_store, ParquetStore) or not columnar_store.root.is_dir() or not await columnar_ready(session):
                    raise JobError("columnar_schema_unavailable", "列式配置或迁移未就绪，未领取任务", 503)
                arrow_engine()
            if not await heartbeat_worker(session, lease):
                await session.rollback()
                return False
            claim = await claim_next(session, lease)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    if claim is None:
        return False
    selected_runner = runner or claim_runner(claim, columnar_store=columnar_store)
    return await execute_claim(session_factory, store, claim, runner=selected_runner, columnar_store=columnar_store)


async def run_claim_process(claim, store, heartbeat, *, columnar_store=None):
    raw = claim.model_dump_json().encode("utf8") + b"\n"
    remaining = (claim.deadline_at - utc_now()).total_seconds()
    if len(raw) > 512 * 1024 or not 0 < remaining <= 300:
        raise JobError("dataset_invalid", "观察任务输入大小或期限无效", 422)
    environment = {**os.environ, "WENYUAN_STOCK_DATASET_ROOT": str(store.root)}
    module = "app.services.stock_research_v2.dataset_process"
    if columnar_store is not None:
        from app.services.stock_research_v2.columnar_store import ParquetStore
        if not isinstance(columnar_store, ParquetStore) or not columnar_store.root.is_dir():
            raise JobError("columnar_store_unavailable", "列式存储根未就绪", 503)
        module = "app.services.stock_research_v2.columnar_process"
        environment["WENYUAN_STOCK_COLUMNAR_ROOT"] = str(columnar_store.root)
        # Native BLAS pools otherwise initialize outside the row-batch budget.
        environment.update({name: "1" for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    process = spawn_bounded_process([sys.executable, "-u", "-m", module], cwd=BACKEND_ROOT, env=environment)
    sender = None
    try:
        async def send():
            await asyncio.to_thread(process.stdin.write, raw)
            await asyncio.to_thread(process.stdin.flush)
            process.stdin.close()
            process.stdin = None
        # Start input writer concurrently so a pipe that stalls cannot bypass supervision.
        sender = asyncio.create_task(send())
        if columnar_store is not None:
            return await supervise_dataset_process(process, heartbeat=heartbeat, timeout_seconds=remaining, columnar=True)
        return await supervise_dataset_process(process, heartbeat=heartbeat, timeout_seconds=remaining)
    finally:
        try:
            await asyncio.to_thread(process.close)
        finally:
            if sender is not None:
                await asyncio.gather(sender, return_exceptions=True)


async def execute_claim(session_factory, store: ArtifactStore, claim: DatasetClaim, *, runner=None, columnar_store=None):
    claim = DatasetClaim.model_validate(claim.model_dump())
    if not isinstance(store, ArtifactStore) or not store.root.is_dir():
        raise JobError("artifact_store_unavailable", "观察存储根未就绪", 503)

    async def transaction(operation):
        async with asyncio.timeout(5), session_factory() as session:
            try:
                value = await operation(session)
                await session.commit()
                return value
            except BaseException:
                await session.rollback()
                raise

    async def heartbeat():
        return await transaction(lambda session: renew_claim(session, claim))

    async def failure(code):
        return await transaction(lambda session: fail_claim(session, claim, code))

    try:
        if not await heartbeat():
            return False
        selected_runner = runner or claim_runner(claim, columnar_store=columnar_store)
        reference = await selected_runner(claim, store, heartbeat)
        if reference is None:
            await failure("worker_failed")
            return False
        async def publish(session):
            if claim.kind != "dataset":
                # A dispatched kernel reports content, never identity: the artifact
                # row is registered here, under the claim's own scope and job key.
                archive = ArchiveService(session, claim.scope, store)
                if reference.shape != "report" or reference.kind != claim.kind:
                    raise JobError("task_kernel_failed", "研究执行器返回的结果形态与任务类型不一致", 422)
                document = store.read_json(reference.report_sha256)
                digest, size = descriptor(document, store.max_bytes)
                if digest != reference.report_sha256 or size != reference.report_byte_size:
                    raise JobError("task_kernel_failed", "研究产物内容与报告指纹不一致", 409)
                # 015's purpose vocabulary predates the task chain and migrations are
                # frozen, so a research report is registered under the only legal
                # purpose; the job row, not the artifact, records what it is.
                row = await archive._register(document, purpose="dataset", key=claim.job_id, request_id=claim.request_id)
                return await finish_report(session, claim, row.id, digest)
            archive = ArchiveService(session, claim.scope, store)
            fixed = reference
            if columnar_store is not None:
                from app.schemas.stock_columnar import ColumnarRef
                from app.services.stock_research_v2.columnar import ColumnarService
                receipt = ColumnarRef.model_validate(reference)
                await ColumnarService(archive, columnar_store).read(receipt, expected_idempotency_key=claim.job_id)
                fixed = receipt.dataset
            return await finish_dataset(session, claim, fixed, archive=archive)
        written = await transaction(publish)
        logger.info("request_id=%s job_id=%s operation=%s_publication persisted=%s model_calls=0",
            claim.request_id, claim.job_id, claim.kind, written)
        return written
    except asyncio.CancelledError:
        try:
            await failure("worker_failed")
        except Exception:
            logger.error("request_id=%s job_id=%s operation=shutdown_persist_failed", claim.request_id, claim.job_id)
        raise
    except Exception as error:
        code = error.code if isinstance(error, JobError) and error.code in {"dataset_invalid", "execution_timeout", *TASK_FAILURE_CODES} else "worker_failed"
        await failure(code)
        logger.warning("request_id=%s job_id=%s operation=execution_failed code=%s", claim.request_id, claim.job_id, code)
        return False
    finally:
        async def release(session):
            await recover_expired(session)
            return await release_claim(session, claim)
        try:
            await transaction(release)
        except Exception:
            logger.error("request_id=%s job_id=%s operation=release_failed", claim.request_id, claim.job_id)
