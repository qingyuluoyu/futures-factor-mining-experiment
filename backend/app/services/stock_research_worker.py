import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from app.schemas.stock_research_jobs import MAX_RESULT_BYTES, MAX_RUNTIME_SECONDS
from app.services.stock_research_jobs import JobError, claim_next_job, finish_job, heartbeat_job, recover_expired_jobs, release_worker_slot, utc_datetime, utc_now


logger = logging.getLogger("qihuo.stock_research_worker")
BACKEND_ROOT = Path(__file__).resolve().parents[2]
MAX_MESSAGE_BYTES = MAX_RESULT_BYTES + 4096


class JobStopped(Exception):
    pass


class ProcessExecutionError(JobError):
    def __init__(self, code: str, message: str, status: int = 500, terminal_status: str = "failed"):
        super().__init__(code, message, status)
        self.terminal_status = terminal_status


async def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=3)


async def supervise_process(process: subprocess.Popen, *, heartbeat, snapshot, timeout_seconds: float, max_message_bytes: int = MAX_MESSAGE_BYTES,
    timeout_message: str = "研究执行超过120秒上限，进程已终止且不会自动重试") -> dict:
    deadline = time.monotonic() + timeout_seconds
    reader = None
    last_heartbeat = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise ProcessExecutionError("execution_timeout", timeout_message, 504, "expired")
            if now - last_heartbeat >= 1:
                if not await asyncio.wait_for(heartbeat(), timeout=max(0.001, deadline - time.monotonic())):
                    raise JobStopped()
                last_heartbeat = now = time.monotonic()
            if reader is None:
                reader = asyncio.create_task(asyncio.to_thread(process.stdout.readline, max_message_bytes + 1))
            done, pending = await asyncio.wait({reader}, timeout=min(0.2, max(0.01, deadline - now)))
            if not done:
                continue
            line = reader.result()
            reader = None
            if not line:
                code = "execution_timeout" if process.poll() == 124 else "worker_exit"
                raise ProcessExecutionError(code, "研究进程提前退出，未保存不完整结果", 504 if code == "execution_timeout" else 500, "expired" if code == "execution_timeout" else "failed")
            if len(line) > max_message_bytes:
                raise ProcessExecutionError("result_too_large", "研究输出超过安全上限，进程已终止", 422)
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("invalid message")
                message_type = message.get("type")
                if message_type == "snapshot" and isinstance(message.get("manifest"), dict):
                    if not await asyncio.wait_for(snapshot(message["manifest"]), timeout=max(0.001, deadline - time.monotonic())):
                        raise JobStopped()
                elif message_type == "result" and isinstance(message.get("result"), dict):
                    return message["result"]
                elif message_type == "error":
                    code, status = message.get("code"), message.get("status")
                    if not isinstance(code, str) or not 1 <= len(code) <= 64 or not isinstance(status, int) or not 400 <= status <= 599 or not isinstance(message.get("message"), str):
                        raise ValueError("invalid error")
                    raise ProcessExecutionError(code, message["message"][:500], status)
                else:
                    raise ValueError("unknown message")
            except (ValueError, UnicodeError, TypeError) as error:
                if isinstance(error, JobError):
                    raise
                raise ProcessExecutionError("worker_protocol", "研究进程输出格式异常，未保存结果", 500) from None
    except TimeoutError:
        raise ProcessExecutionError("execution_timeout", timeout_message, 504, "expired") from None
    finally:
        await stop_process(process)
        if reader is not None:
            with suppress(Exception, asyncio.CancelledError):
                await reader
        if process.stdout:
            process.stdout.close()
        if process.stdin:
            process.stdin.close()


class StockResearchWorker:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.task: asyncio.Task | None = None
        self.last_success = 0.0

    @property
    def healthy(self) -> bool:
        return self.task is not None and not self.task.done() and time.monotonic() - self.last_success < 15

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="stock-research-worker")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def run(self) -> None:
        while True:
            try:
                executed = await self.run_once()
                if not executed:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("operation=stock_research_worker error_type=%s", type(error).__name__)
                await asyncio.sleep(2)

    async def run_once(self) -> bool:
        async with asyncio.timeout(5), self.session_factory() as session:
            job = await claim_next_job(session)
            await session.commit()
            self.last_success = time.monotonic()
            if job is None:
                return False
            job_id, worker_token = job.id, job.worker_token
            job_request, user_id, mode = job.request_id, job.user_id, job.trading_mode
            deadline_at = utc_datetime(job.deadline_at)
            payload = {"kind": job.kind, "parameters": job.params_json, "deadline_at": deadline_at.isoformat()}
        started = time.monotonic()
        logger.info("request_id=%s job_id=%s user_id=%s mode=%s operation=research_started attempts=1", job_request, job_id, user_id, mode)

        async def heartbeat(manifest=None):
            async with asyncio.timeout(5), self.session_factory() as session:
                active = await heartbeat_job(session, job_id, worker_token, manifest)
                await session.commit()
                self.last_success = time.monotonic()
                return active

        async def persist(**outcome):
            async with asyncio.timeout(5), self.session_factory() as session:
                written = await finish_job(session, job_id, worker_token, **outcome)
                await session.commit()
                return written

        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", "-m", "app.services.stock_research_process"],
                cwd=str(BACKEND_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            process.stdin.write(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode() + b"\n")
            process.stdin.flush()
            process.stdin.close()
            process.stdin = None
            result = await supervise_process(process, heartbeat=heartbeat, snapshot=heartbeat, timeout_seconds=max(0, (deadline_at - utc_now()).total_seconds()))
            written = await persist(result=result)
            logger.info("request_id=%s job_id=%s user_id=%s mode=%s operation=research_completed persisted=%s elapsed_ms=%.1f model_calls=0", job_request, job_id, user_id, mode, written, (time.monotonic() - started) * 1000)
        except JobStopped:
            logger.info("request_id=%s job_id=%s user_id=%s mode=%s operation=research_stopped", job_request, job_id, user_id, mode)
        except asyncio.CancelledError:
            if process is not None:
                await stop_process(process)
            try:
                await persist(error=("worker_shutdown", "执行器正在关闭，任务未自动重试；请核对状态后重新创建", 503))
            except Exception as error:
                logger.error("request_id=%s job_id=%s operation=shutdown_persist error_type=%s", job_request, job_id, type(error).__name__)
            raise
        except JobError as error:
            await persist(error=(error.code, error.message, error.status), terminal_status=getattr(error, "terminal_status", "failed"))
            logger.warning("request_id=%s job_id=%s user_id=%s mode=%s operation=research_failed error_type=%s elapsed_ms=%.1f", job_request, job_id, user_id, mode, error.code, (time.monotonic() - started) * 1000)
        except Exception as error:
            if process is not None:
                await stop_process(process)
            await persist(error=("worker_unavailable", "研究执行器发生异常，未保存结果，请稍后核对任务状态", 503))
            logger.error("request_id=%s job_id=%s user_id=%s mode=%s operation=research_failed error_type=%s", job_request, job_id, user_id, mode, type(error).__name__)
        finally:
            if process is not None:
                await stop_process(process)
                if process.stdout and not process.stdout.closed:
                    process.stdout.close()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            try:
                async with asyncio.timeout(5), self.session_factory() as session:
                    await recover_expired_jobs(session)
                    await release_worker_slot(session, job_id, worker_token)
                    await session.commit()
            except Exception as error:
                logger.error("request_id=%s job_id=%s operation=release_compute_slot error_type=%s", job_request, job_id, type(error).__name__)
        return True
