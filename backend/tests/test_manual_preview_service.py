import inspect

import httpx
import pytest
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_manual_preview_states_limits_and_preserves_original_handlers():
    from scripts.preview_service import install_preview_boundary

    app = FastAPI()
    calls = []

    @app.api_route('/api/{path:path}', methods=['GET', 'POST', 'DELETE'])
    async def original(path: str):
        calls.append(path)
        return {'original': True}

    install_preview_boundary(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        status = await client.get('/preview/status')
        assert status.status_code == 200
        assert status.json()['runtime'] == 'manual-no-workers'
        assert status.json()['stock_chat_available'] is False
        assert status.json()['release_verified'] is False
        assert status.headers['cache-control'] == 'no-store'
        for path in ['/api/stock/research/jobs', '/api/ai-trading/tasks', '/api/factor-mining/v2/tasks', '/api/stock/sync', '/api/admin/data', '/api/ai-anchor/start']:
            response = await client.post(path)
            assert response.status_code == 503
            assert response.json()['code'] == 'manual_preview_paused'
            assert response.headers['x-wenyuan-preview'] == 'manual-no-workers'
        assert not calls
        for path in ['/api/news/rb2701', '/api/news/article', '/api/market/kline', '/api/market/kline/bundle', '/api/admin/unknown']:
            response = await client.get(path)
            assert response.status_code == 503
        assert not calls
        for method, path in [('POST', '/api/auth/login'), ('POST', '/api/ai/chat'), ('GET', '/api/stock/market/boards'), ('POST', '/api/stock/replay/accounts')]:
            response = await client.request(method, path)
            assert response.status_code == 200
            assert response.json() == {'original': True}
        assert len(calls) == 4


def test_preview_entry_never_runs_production_background_lifecycle():
    from scripts import preview_service

    source = inspect.getsource(preview_service.preview_lifespan)
    assert 'verify_database_schema' in source
    assert 'engine.dispose' in source
    assert 'aclose' in source
    for forbidden in ['start_scheduler', '_init_tables', 'create_all', 'start_gp_worker', 'StockResearchWorker', '_start_stock_eod', '_start_stock_archive']:
        assert forbidden not in source


def test_preview_write_allowlist_is_exact_not_similar_prefixes():
    from scripts.preview_service import permits_manual_write

    assert permits_manual_write('/api/ai/chat')
    assert permits_manual_write('/api/users/me/market')
    assert permits_manual_write('/api/ai/models/providers/aaaaaaaa-1111-2222-3333-444444444444')
    assert permits_manual_write('/api/stock/replay/accounts/abc/orders')
    for path in ['/api/ai/chat/anything', '/api/auth/login-extra', '/api/stock/replayevil/accounts', '/api/stock/replay/accounts/abc/admin', '/api/stock/backtest']:
        assert not permits_manual_write(path)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['schema', 'redis_ping', 'redis_close', 'none'])
async def test_preview_cleanup_on_failure(monkeypatch, failure):
    from scripts.preview_service import preview_lifespan
    from types import SimpleNamespace
    import app.core.config as config
    import app.core.database as database
    import app.services.database_readiness as readiness
    import redis.asyncio as redis_module

    events = []

    class Settings:
        postgres_host, postgres_port = '127.0.0.1', 5433
        stock_eod_enabled, stock_daily_archive_enabled = False, False
        redis_host, redis_port, redis_url = '127.0.0.1', 6380, 'redis://127.0.0.1:6380/0'

        def model_copy(self, update):
            assert update == {'app_env': 'manual_preview'}
            return self

    class Engine:
        async def dispose(self):
            events.append('engine_disposed')

    class Connection:
        async def ping(self):
            events.append('ping')
            if failure == 'redis_ping':
                raise RuntimeError('redis_ping')

        async def aclose(self):
            events.append('redis_closed')
            if failure == 'redis_close':
                raise RuntimeError('redis_close')

    async def verify(engine):
        events.append('schema')
        if failure == 'schema':
            raise RuntimeError('schema')

    monkeypatch.setattr(config, 'load_settings', Settings)
    monkeypatch.setattr(database, 'create_database_engine', lambda settings: Engine())
    monkeypatch.setattr(database, 'create_session_factory', lambda engine: object())
    monkeypatch.setattr(readiness, 'verify_database_schema', verify)
    monkeypatch.setattr(redis_module.Redis, 'from_url', lambda *args, **kwargs: Connection())
    application = SimpleNamespace(state=SimpleNamespace())

    async def run():
        async with preview_lifespan(application):
            assert application.state.stock_research_worker is None
            assert application.state.stock_eod_worker is None
            events.append('served')

    if failure != 'none':
        with pytest.raises(RuntimeError, match=failure):
            await run()
    else:
        await run()
    assert events[-1] == 'engine_disposed'
    if failure != 'schema':
        assert 'redis_closed' in events


@pytest.mark.asyncio
async def test_preview_reads_existing_eod_without_starting_collection(monkeypatch):
    from scripts.preview_service import initialize_stock_reads
    from types import SimpleNamespace
    import scripts.migrate_stock_eod as eod
    import scripts.migrate_stock_daily_archive as archive
    import scripts.migrate_stock_archive_jobs as jobs

    observed = []

    async def check_only(engine, *, check=False):
        assert check is True
        observed.append(engine)

    for module, function in [(eod, 'migrate_stock_eod'), (archive, 'migrate_stock_daily_archive'), (jobs, 'migrate_stock_archive_jobs')]:
        monkeypatch.setattr(module, function, check_only)
    application = SimpleNamespace(state=SimpleNamespace())
    settings = SimpleNamespace(stock_eod_enabled=True, stock_daily_archive_enabled=True)
    engine = object()
    await initialize_stock_reads(application, settings, engine)
    assert application.state.stock_eod_enabled is True
    assert application.state.stock_eod_sync_enabled is False
    assert application.state.stock_eod_catchup_enabled is False
    assert application.state.stock_archive_ready is True
    assert application.state.stock_archive_worker is None
    assert application.state.stock_eod_worker is None
    assert observed == [engine, engine, engine]


@pytest.mark.asyncio
async def test_actual_preview_router_blocks_external_read_paths_before_handlers():
    from scripts.preview_service import create_preview_app
    from tests.test_stock_research_snapshots import snapshot_database

    application = create_preview_app()
    async with snapshot_database() as (factory, database):
        class TransactionSession(factory):
            async def rollback(self):
                self.session.rollback()

            async def commit(self):
                self.session.commit()

        application.state.session_factory = TransactionSession
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
            for path in ['/api/news/rb2701', '/api/news/article?url=https://example.com', '/api/market/kline?symbol=rb2701', '/api/market/kline/bundle?symbol=rb2701']:
                response = await client.get(path)
                assert response.status_code == 503
                assert response.json()['code'] == 'manual_preview_paused'
            for method, path in [('GET', '/api/stock/market/boards'), ('PUT', '/api/users/me/market'), ('POST', '/api/ai/models/providers/aaaaaaaa-1111-2222-3333-444444444444')]:
                response = await client.request(method, path, json={})
                assert response.status_code == 401


def test_preview_transport_logs_redact_query_credentials():
    import logging
    from scripts.preview_service import PreviewLogFilter

    record = logging.LogRecord('uvicorn.error', logging.INFO, '', 0, '%s - WebSocket %s',
        ('client', '/ws/market?token=private-jwt&mode=live&api_key=private-key&key=public-stream-secret'), None)
    assert PreviewLogFilter().filter(record)
    assert 'private-jwt' not in record.getMessage()
    assert 'private-key' not in record.getMessage()
    assert 'public-stream-secret' not in record.getMessage()
    assert 'mode=live' in record.getMessage()
