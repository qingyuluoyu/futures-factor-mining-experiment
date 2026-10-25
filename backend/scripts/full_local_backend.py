import asyncio
import logging
import os
from pathlib import Path

from sqlalchemy import text


async def preflight(settings):
    from scripts.migrate_stock_replay import create_replay_engine
    from app.services.database_readiness import verify_database_schema

    if (settings.postgres_host != '127.0.0.1' or settings.postgres_port != 5433
            or settings.redis_host != '127.0.0.1' or settings.redis_port != 6380):
        raise RuntimeError('Dedicated local database and Redis endpoints required')
    engine = create_replay_engine(settings)
    try:
        async with asyncio.timeout(15):
            await verify_database_schema(engine)
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text('SET TRANSACTION READ ONLY'))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            active = await connection.scalar(text(
                "SELECT count(*) FROM ai_trading_tasks WHERE model_row_id IS NOT NULL "
                "AND (status='running' OR (status='paused' AND pause_reason='market_closed'))"
            ))
            anchors = await connection.scalar(text("SELECT count(*) FROM ai_anchor_tasks WHERE status='running'"))
            if active or anchors:
                raise RuntimeError('Automatic paid AI tasks require a separate explicit budget decision')
    finally:
        await engine.dispose()


def install_runtime_status(application):
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @application.get('/local-runtime/status')
    async def runtime_status(request: Request):
        from app.services import scheduler

        def running(name):
            task = getattr(getattr(request.app.state, name, None), 'task', None)
            return task is not None and not task.done()

        return JSONResponse({
            'runtime': 'original-main-lifecycle', 'preview_restrictions': False,
            'scheduler_master': bool(scheduler._is_master),
            'stock_research_worker': running('stock_research_worker'),
            'stock_eod_worker': running('stock_eod_worker'),
            'stock_archive_worker': running('stock_archive_worker'),
            'gp_checkpoint_worker': running('gp_checkpoint_worker'),
            'stock_eod_enabled': bool(getattr(request.app.state, 'stock_eod_enabled', False)),
            'stock_eod_sync_enabled': bool(getattr(request.app.state, 'stock_eod_sync_enabled', False)),
            'release_verified': False,
        }, headers={'Cache-Control': 'no-store'})


def run_backend():
    from local_service import configure_environment
    configure_environment(Path(__file__).resolve().parents[2])
    os.environ['APP_ENV'] = 'local_runtime'
    from app.core.config import load_settings
    from scripts.preview_service import PreviewLogFilter
    import uvicorn

    asyncio.run(preflight(load_settings()))
    for name in ('uvicorn.error', 'uvicorn.access'):
        logging.getLogger(name).addFilter(PreviewLogFilter())
    from app.main import app
    install_runtime_status(app)
    uvicorn.run('app.main:app', host='127.0.0.1', port=8002, access_log=False)


if __name__ == '__main__':
    run_backend()
