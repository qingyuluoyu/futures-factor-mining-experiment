"""Explicit, one-lease orchestration of the stock research v2 task chain.

The worker never acquires or steals a lease implicitly: it re-acquires the
016 single-instance lease on every cycle, so a lease it lost to another process is
re-earned once that lease expires, never fought over.  Every claim still goes
through the fenced ``run_dataset_once`` path, and the loop is only ever started by
an explicit ``app/main.py`` lifespan call behind a default-OFF Settings switch.

Stopping mid-lease deliberately does not clear the lease: it expires on its own
within ``LEASE_SECONDS`` and ``acquire_worker`` refuses to hand a live one to
anybody else, so an unclean shutdown can never be mistaken for a free slot.
"""
import asyncio
import logging
from uuid import uuid4

from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.dataset_worker import run_dataset_once
from app.services.stock_research_v2.jobs import LEASE_SECONDS, acquire_worker, heartbeat_worker

logger = logging.getLogger("qihuo.stock_research_v2_worker")
IDLE_SECONDS = 5
BACKOFF_SECONDS = 15
STEP_SECONDS = 10


def _schedule(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 3600:
        raise JobError("worker_schedule_invalid", "执行器轮询间隔无效", 422)
    return value


class StockResearchV2Worker:
    def __init__(self, session_factory, store: ArtifactStore, *, instance_id=None, idle_seconds=IDLE_SECONDS,
                 backoff_seconds=BACKOFF_SECONDS, once=None):
        if not isinstance(store, ArtifactStore):
            raise JobError("artifact_store_unavailable", "观察存储根未就绪", 503)
        self.session_factory = session_factory
        self.store = store
        self.instance_id = instance_id or uuid4()
        self.idle_seconds = _schedule(idle_seconds, "idle_seconds")
        self.backoff_seconds = _schedule(backoff_seconds, "backoff_seconds")
        self.once = once or run_dataset_once
        self.task = None

    def start(self):
        if self.task is not None and not self.task.done():
            raise JobError("worker_already_started", "执行器已在运行", 409)
        self.task = asyncio.create_task(self.run(), name="stock-research-v2-worker")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _lease(self):
        """Acquire the single-instance lease, or report why it is unavailable."""
        try:
            async with asyncio.timeout(STEP_SECONDS), self.session_factory() as session:
                try:
                    lease = await acquire_worker(session, self.instance_id)
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise
            return lease
        except JobError as error:
            if error.code != "worker_already_active":
                logger.warning("operation=v2_worker_lease error_code=%s", error.code)
            return None
        except Exception as error:
            logger.error("operation=v2_worker_lease error_type=%s", type(error).__name__)
            return None

    async def _renew(self, lease):
        try:
            async with asyncio.timeout(STEP_SECONDS), self.session_factory() as session:
                try:
                    alive = await heartbeat_worker(session, lease)
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise
            return alive
        except Exception as error:
            logger.error("operation=v2_worker_heartbeat error_type=%s", type(error).__name__)
            return False

    async def _cycle(self, lease):
        while True:
            if not await self._renew(lease):
                logger.warning("operation=v2_worker_lease_lost instance_id=%s", self.instance_id)
                return
            await self.once(self.session_factory, self.store, lease)
            await asyncio.sleep(self.idle_seconds)

    async def run(self):
        while True:
            lease = await self._lease()
            if lease is None:
                await asyncio.sleep(self.backoff_seconds)
                continue
            logger.info("operation=v2_worker_lease_acquired instance_id=%s lease_seconds=%d", self.instance_id, LEASE_SECONDS)
            try:
                await self._cycle(lease)
            except asyncio.CancelledError:
                logger.info("operation=v2_worker_stopped instance_id=%s lease_expires_in_seconds=%d", self.instance_id, LEASE_SECONDS)
                raise
            except Exception as error:
                logger.error("operation=v2_worker_cycle error_type=%s", type(error).__name__)
            await asyncio.sleep(self.backoff_seconds)
