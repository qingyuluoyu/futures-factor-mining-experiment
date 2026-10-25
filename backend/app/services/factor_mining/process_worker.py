import asyncio
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

from app.services.factor_lab.checkpoint import canonical_bytes
from app.services.factor_mining.checkpoint_store import CheckpointError, CheckpointStore
from app.services.factor_mining.jobs import GpJobError, aware, claim_generation, finish_generation, heartbeat_generation, now, recover_leases, release_stopped
from app.services.stock_research_worker import JobStopped, ProcessExecutionError, stop_process, supervise_process


ROOT = Path(__file__).resolve().parents[3]
MAX_INPUT_BYTES = 13 * 1024 * 1024
MAX_MESSAGE_BYTES = 10 * 1024 * 1024 + 4096
logger = logging.getLogger("qihuo.gp_worker")


class StopNotConfirmed(GpJobError):
    def __init__(self):
        super().__init__("process_stop_unconfirmed", 503)


async def execute_generation(payload, *, heartbeat):
    encoded = canonical_bytes(payload)
    remaining = (datetime.fromisoformat(payload["deadline_at"]) - datetime.now(timezone.utc)).total_seconds()
    if len(encoded) > MAX_INPUT_BYTES or remaining <= 0:
        raise GpJobError("process_budget_invalid", 422)
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE") if name in os.environ}
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    process = subprocess.Popen([sys.executable, "-u", "-m", "app.services.gp_generation_process", payload["deadline_at"]], cwd=str(ROOT), env=environment,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    writer = None
    try:
        def write_payload():
            process.stdin.write(encoded + b"\n")
            process.stdin.flush()
            process.stdin.close()
        writer = asyncio.create_task(asyncio.to_thread(write_payload))
        await asyncio.wait_for(asyncio.shield(writer), timeout=min(10, remaining))
        process.stdin = None
        remaining = (datetime.fromisoformat(payload["deadline_at"]) - datetime.now(timezone.utc)).total_seconds()
        return await supervise_process(process, heartbeat=heartbeat, snapshot=heartbeat, timeout_seconds=max(0, remaining),
            max_message_bytes=MAX_MESSAGE_BYTES, timeout_message="GP单代达到运行预算，进程已终止且不自动重试")
    finally:
        try:
            await stop_process(process)
        finally:
            if process.poll() is None:
                raise StopNotConfirmed()
            if writer is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await writer
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.stdout and not process.stdout.closed:
                process.stdout.close()


class GpCheckpointWorker:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.task = None
        self.last_success = 0.0

    @property
    def healthy(self):
        return self.task is not None and not self.task.done() and time.monotonic() - self.last_success < 15

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="gp-checkpoint-worker")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def run(self):
        while True:
            try:
                worked = await self.run_once()
                if not worked:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("operation=gp_worker_cycle error_type=%s", type(error).__name__)
                await asyncio.sleep(2)

    async def run_once(self):
        async with asyncio.timeout(5), self.session_factory() as session:
            job = await claim_generation(session)
            await session.commit()
            self.last_success = time.monotonic()
            if job is None:
                return False
            identifier, token, user_id, mode = job.id, job.lease_token, job.user_id, job.trading_mode
            input_id, generation, deadline, request_id = job.input_id, job.generation, aware(job.generation_deadline), job.request_id
        can_release, error_code = True, None
        async def heartbeat(*args):
            async with asyncio.timeout(5), self.session_factory() as session:
                active = await heartbeat_generation(session, identifier, token)
                await session.commit()
                self.last_success = time.monotonic()
                return active
        try:
            async with asyncio.timeout(5), self.session_factory() as session:
                frozen = await CheckpointStore(session, user_id, "futures", mode).load(input_id)
                if frozen.generation != generation:
                    raise GpJobError("input_generation_changed")
                payload = {"input": frozen.input_json, "config": frozen.config_json, "checkpoint": frozen.checkpoint_json,
                    "generation": generation, "deadline_at": deadline.isoformat()}
            outcome = await execute_generation(payload, heartbeat=heartbeat)
            if not isinstance(outcome, dict) or set(outcome) != {"generation", "checkpoint", "champions"} or outcome["generation"] != generation + 1:
                raise GpJobError("invalid_process_result", 422)
            async with asyncio.timeout(10), self.session_factory() as session:
                await finish_generation(session, identifier, token, outcome["checkpoint"], outcome["champions"])
                await session.commit()
        except StopNotConfirmed:
            can_release, error_code = False, "process_stop_unconfirmed"
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            error_code = "worker_shutdown"
            raise
        except JobStopped:
            error_code = "job_stopped"
        except (GpJobError, CheckpointError, ProcessExecutionError) as error:
            error_code = error.code
        except Exception as error:
            error_code = "gp_worker_unavailable"
            logger.error("request_id=%s job_id=%s operation=gp_generation_error error_type=%s", request_id, identifier, type(error).__name__)
        finally:
            if can_release:
                async with asyncio.timeout(5), self.session_factory() as session:
                    await release_stopped(session, identifier, token, error_code=error_code)
                    await session.commit()
            self.last_success = time.monotonic()
            logger.info("request_id=%s job_id=%s user_id=%s mode=%s operation=gp_generation_done error_code=%s stopped=%s source_calls=0 model_calls=0", request_id, identifier, user_id, mode, error_code, can_release)
        return True
