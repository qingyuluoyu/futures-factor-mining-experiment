import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
import subprocess
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


RUNTIME = 'manual-no-workers'


class PreviewLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r'([?&](?:token|access_token|refresh_token|api_key|key)=)[^&\s\"]+',
            r'\1[REDACTED]', record.getMessage(), flags=re.IGNORECASE)
        record.args = ()
        return True


WRITE_PATHS = (
    r'/api/auth/(?:login|register|refresh)',
    r'/api/users/me(?:/notify-settings|/market)?',
    r'/api/ai/chat',
    r'/api/ai/(?:providers|models)(?:/[a-zA-Z0-9-]+(?:/test)?)?',
    r'/api/ai/models/providers/[a-zA-Z0-9-]+',
    r'/api/ai/conversations(?:/[a-zA-Z0-9-]+(?:/messages)?)?',
    r'/api/paper(?:/[a-zA-Z0-9_-]+)*',
    r'/api/stock/replay/accounts(?:/[a-zA-Z0-9-]+(?:/step|/orders(?:/[a-zA-Z0-9-]+/cancel)?)?)?',
)
READ_PATHS = (
    r'/(?:health|preview/status)',
    r'/api/auth/(?:registration-status|me)',
    r'/api/users/me(?:/[a-zA-Z0-9_-]+)*',
    r'/api/stock(?:/[a-zA-Z0-9_-]+)*',
    r'/api/ai/(?:models|providers)(?:/presets)?',
    r'/api/ai/(?:chat/capabilities|tools)',
    r'/api/ai/conversations(?:/[a-zA-Z0-9-]+(?:/messages)?)?',
    r'/api/paper(?:/[a-zA-Z0-9_-]+)*',
    r'/api/market/(?:channel-status|quotes|session-status|contracts(?:/by-code|/search)?)',
    r'/api/(?:notifications|watchlist|ai-trading|factor-mining|backtest|strategy-folders)(?:/[a-zA-Z0-9_-]+)*',
)


def permits_manual_write(path: str) -> bool:
    return any(re.fullmatch(pattern, path) for pattern in WRITE_PATHS)


def install_preview_boundary(application: FastAPI) -> None:
    @application.middleware('http')
    async def boundary(request: Request, call_next):
        allowed = request.method == 'OPTIONS' or (
            any(re.fullmatch(pattern, request.url.path) for pattern in READ_PATHS)
            if request.method in {'GET', 'HEAD'} else permits_manual_write(request.url.path)
        )
        if not allowed:
            response = JSONResponse({
                'detail': '手动预览暂停此入口，避免外源调用或无worker的任务写入；本请求未执行。正式运行需单独验证服务和预算。',
                'code': 'manual_preview_paused',
            }, status_code=503)
        else:
            response = await call_next(request)
        response.headers['X-Wenyuan-Preview'] = RUNTIME
        return response

    @application.get('/preview/status')
    async def preview_status():
        return JSONResponse({
            'runtime': RUNTIME,
            'background_workers': False,
            'automatic_source_calls': False,
            'chat_path': 'original_futures_agent_with_rag_memory_and_tools',
            'chat_subcall_cost_measured': False,
            'stock_chat_available': False,
            'release_verified': False,
            'schema_validation': 'table_column_presence_only',
            'notice': '原权限仍生效；只供手动真实数据预览，不是正式全服务验收，期货实时行情和后台任务未启动。',
        }, headers={'Cache-Control': 'no-store'})


async def initialize_stock_reads(application: FastAPI, settings, engine) -> None:
    from scripts.migrate_stock_eod import migrate_stock_eod
    from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
    from scripts.migrate_stock_archive_jobs import migrate_stock_archive_jobs

    application.state.stock_eod_enabled = False
    application.state.stock_eod_sync_enabled = False
    application.state.stock_eod_catchup_enabled = False
    application.state.stock_eod_error_code = None
    application.state.stock_eod_worker = None
    application.state.stock_archive_enabled = bool(settings.stock_daily_archive_enabled)
    application.state.stock_archive_ready = False
    application.state.stock_archive_worker = None
    if settings.stock_eod_enabled or settings.stock_daily_archive_enabled:
        async with asyncio.timeout(10):
            await migrate_stock_eod(engine, check=True)
        application.state.stock_eod_enabled = bool(settings.stock_eod_enabled)
    if settings.stock_daily_archive_enabled:
        async with asyncio.timeout(10):
            await migrate_stock_daily_archive(engine, check=True)
            await migrate_stock_archive_jobs(engine, check=True)
        application.state.stock_archive_ready = True


@asynccontextmanager
async def preview_lifespan(application: FastAPI):
    from redis.asyncio import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from app.core.config import load_settings
    from app.core.database import create_database_engine, create_session_factory
    from app.services.database_readiness import verify_database_schema

    settings = load_settings().model_copy(update={'app_env': 'manual_preview'})
    if settings.postgres_host != '127.0.0.1' or settings.postgres_port != 5433:
        raise RuntimeError('Manual preview requires the dedicated local PostgreSQL endpoint')
    if settings.redis_host != '127.0.0.1' or settings.redis_port != 6380:
        raise RuntimeError('Manual preview requires the dedicated local Redis endpoint')
    engine = create_database_engine(settings)
    redis_connection = None
    try:
        async with asyncio.timeout(15):
            await verify_database_schema(engine)
        redis_connection = Redis.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
            retry=Retry(NoBackoff(), 0),
        )
        async with asyncio.timeout(3):
            await redis_connection.ping()
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
        application.state.redis = redis_connection
        application.state.stock_research_worker = None
        application.state.gp_checkpoint_worker = None
        application.state.stock_eod_worker = None
        application.state.stock_archive_worker = None
        await initialize_stock_reads(application, settings, engine)
        yield
    finally:
        try:
            if redis_connection is not None:
                await redis_connection.aclose()
        finally:
            await engine.dispose()


def create_preview_app() -> FastAPI:
    from app.main import create_app

    application = create_app()
    application.router.lifespan_context = preview_lifespan
    install_preview_boundary(application)
    return application


def main() -> int:
    parser = argparse.ArgumentParser(description='Local manual preview only; no background workers or automatic data/model calls')
    parser.add_argument('service', choices=('backend', 'frontend'))
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if arguments.service == 'frontend':
        environment = os.environ.copy()
        environment['BACKEND_URL'] = 'http://127.0.0.1:8002'
        return subprocess.call([
            'node', str(project_root / 'frontend/node_modules/next/dist/bin/next'),
            'dev', '--hostname', '127.0.0.1', '--port', '3000',
        ], cwd=project_root / 'frontend', env=environment)
    from local_service import configure_environment
    configure_environment(project_root)
    import uvicorn

    for name in ('uvicorn.error', 'uvicorn.access'):
        logging.getLogger(name).addFilter(PreviewLogFilter())
    uvicorn.run(create_preview_app(), host='127.0.0.1', port=8002, access_log=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
