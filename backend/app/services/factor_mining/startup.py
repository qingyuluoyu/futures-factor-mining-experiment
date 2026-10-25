import asyncio
import logging

from app.services.factor_mining.process_worker import GpCheckpointWorker
from scripts.migrate_gp_jobs import migrate_gp_jobs


logger = logging.getLogger("qihuo.gp_startup")


async def start_gp_worker(application, session_factory, settings):
    application.state.gp_checkpoint_worker = None
    if not getattr(settings, "gp_jobs_enabled", False):
        return
    try:
        async with asyncio.timeout(10), session_factory() as session:
            await migrate_gp_jobs(await session.connection(), check=True)
        worker = GpCheckpointWorker(session_factory)
        application.state.gp_checkpoint_worker = worker
        worker.start()
    except Exception as error:
        logger.error("operation=gp_startup_not_ready error_type=%s", type(error).__name__)
