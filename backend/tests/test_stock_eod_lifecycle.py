from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib
import logging

from fastapi import FastAPI
import pytest

from app.core.config import Settings


def test_eod_flags_default_off_and_accept_explicit_environment(monkeypatch):
    for name in ("STOCK_EOD_ENABLED", "STOCK_EOD_SYNC_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    values = {"postgres_user": "qa", "postgres_password": "qa", "postgres_db": "qa", "postgres_host": "localhost", "postgres_port": 5433, "redis_host": "localhost", "redis_port": 6379, "jwt_secret_key": "qa"}
    defaults = Settings(_env_file=None, **values)
    assert hasattr(defaults, "stock_eod_enabled")
    assert defaults.stock_eod_enabled is False and defaults.stock_eod_sync_enabled is False
    monkeypatch.setenv("STOCK_EOD_ENABLED", "true")
    monkeypatch.setenv("STOCK_EOD_SYNC_ENABLED", "true")
    enabled = Settings(_env_file=None, **values)
    assert enabled.stock_eod_enabled is True and enabled.stock_eod_sync_enabled is True
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "STOCK_EOD_ENABLED=false" in example
    assert "STOCK_EOD_SYNC_ENABLED=false" in example


@pytest.fixture
def lifecycle(monkeypatch):
    module = importlib.import_module("app.main")
    assert hasattr(module, "migrate_stock_eod"), "EOD lifecycle readiness must reuse the migration checker"
    events = []
    settings = SimpleNamespace(log_level="INFO", app_env="production", stock_research_jobs_enabled=False, stock_eod_enabled=False, stock_eod_sync_enabled=False, tushare_token="qa-source-token")
    state = SimpleNamespace(settings=settings, migration_error=None, schema_error=None, start_error=None, stop_error=None)

    async def schema_check(engine):
        events.append("base_schema_readiness")
        if state.schema_error:
            raise state.schema_error
        return {"tables_checked": 39, "columns_checked": 496, "validation": "table_column_presence_only"}

    async def check(engine, *, check):
        events.append("eod_readiness")
        assert check is True
        if state.migration_error:
            raise state.migration_error
        return {"checked": True, "applied": False}

    checker = AsyncMock(side_effect=check)

    class Worker:
        def __init__(self, factory, source, *, catchup_enabled=False):
            self.factory, self.source = factory, source
            events.append("eod_construct")

        def start(self):
            events.append("eod_start")
            if state.start_error:
                raise state.start_error

        async def stop(self):
            events.append("eod_stop")
            if state.stop_error:
                raise state.stop_error

    class ResearchWorker:
        def __init__(self, factory):
            events.append("research_construct")

        def start(self):
            events.append("research_start")

        async def stop(self):
            events.append("research_stop")

    class Engine:
        async def dispose(self):
            events.append("engine_dispose")

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace()

    monkeypatch.setattr(module, "load_settings", lambda: settings)
    monkeypatch.setattr(module, "setup_logger", lambda _: logging.getLogger("qihuo.eod_lifecycle_test"))
    monkeypatch.setattr(module, "create_database_engine", lambda _: Engine())
    monkeypatch.setattr(module, "create_session_factory", lambda _: sessions)
    monkeypatch.setattr(module, "create_redis_connection", AsyncMock(return_value=object()))
    state.legacy_init = AsyncMock(side_effect=AssertionError("Legacy startup mutations must not run"))
    state.schema_check = AsyncMock(side_effect=schema_check)
    monkeypatch.setattr(module, "_init_tables", state.legacy_init)
    monkeypatch.setattr(module, "verify_database_schema", state.schema_check)
    monkeypatch.setattr(module, "set_session_factory", lambda _: None)
    for name in ("start_scheduler", "start_notify_subscriber", "start_big_order_subscriber", "start_ops_alert_subscriber", "stop_ops_alert_subscriber", "stop_big_order_subscriber", "stop_notify_subscriber", "stop_scheduler", "close_redis_connection"):
        monkeypatch.setattr(module, name, AsyncMock())
    monkeypatch.setattr(module, "migrate_stock_eod", checker)
    monkeypatch.setattr(module, "StockEodWorker", Worker)
    monkeypatch.setattr(module, "TeajoinEodSource", lambda token: SimpleNamespace(token=token))
    monkeypatch.setattr(module, "StockResearchWorker", ResearchWorker)
    monkeypatch.setattr(module, "research_ready", AsyncMock(return_value=True))
    return module, FastAPI(), state, events, checker


@pytest.mark.parametrize("sync_enabled", [False, True])
async def test_disabled_read_flag_never_checks_missing_tables_or_starts_sync(lifecycle, sync_enabled):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_sync_enabled = sync_enabled
    async with module.lifespan(app):
        assert app.state.stock_eod_enabled is False
        assert app.state.stock_eod_worker is None
        checker.assert_not_awaited()
    assert "eod_start" not in events
    assert events[-1] == "engine_dispose"


async def test_read_only_mode_checks_migration_and_never_constructs_source_or_worker(lifecycle):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = True
    state.settings.tushare_token = ""
    async with module.lifespan(app):
        assert app.state.stock_eod_enabled is True
        assert app.state.stock_eod_worker is None
        checker.assert_awaited_once_with(app.state.engine, check=True)
    assert "eod_construct" not in events


@pytest.mark.parametrize("failure", [RuntimeError("checksum mismatch with qa-secret"), RuntimeError("missing table qa-secret"), RuntimeError("schema mismatch qa-secret")])
async def test_readiness_failure_disables_reads_and_sync_without_breaking_research(lifecycle, caplog, failure):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = state.settings.stock_eod_sync_enabled = True
    state.settings.stock_research_jobs_enabled = True
    state.migration_error = failure
    with caplog.at_level(logging.INFO):
        async with module.lifespan(app):
            assert app.state.stock_eod_enabled is False
            assert app.state.stock_eod_worker is None
            assert app.state.stock_eod_error_code
            assert app.state.stock_research_worker is not None
    assert "research_start" in events and "research_stop" in events
    assert "eod_start" not in events
    assert "RuntimeError" in caplog.text and "qa-secret" not in caplog.text


@pytest.mark.parametrize("token", ["", "   "])
async def test_sync_requires_nonempty_token_but_keeps_migrated_read_access(lifecycle, token):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = state.settings.stock_eod_sync_enabled = True
    state.settings.tushare_token = token
    async with module.lifespan(app):
        assert app.state.stock_eod_enabled is True
        assert app.state.stock_eod_worker is None
        assert app.state.stock_eod_error_code
    assert "eod_construct" not in events


async def test_enabled_worker_is_stored_and_stopped_before_database_disposal(lifecycle):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = state.settings.stock_eod_sync_enabled = True
    async with module.lifespan(app):
        assert app.state.stock_eod_enabled is True
        assert app.state.stock_eod_worker.source.token == "qa-source-token"
        assert app.state.stock_eod_error_code is None
    assert events.index("eod_readiness") < events.index("eod_construct") < events.index("eod_start")
    assert events.index("eod_stop") < events.index("engine_dispose")


@pytest.mark.parametrize("phase", ["start", "stop"])
async def test_worker_failure_is_redacted_and_keeps_original_shutdown_running(lifecycle, caplog, phase):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = state.settings.stock_eod_sync_enabled = True
    state.settings.stock_research_jobs_enabled = True
    setattr(state, f"{phase}_error", RuntimeError("source-token=qa-secret"))
    with caplog.at_level(logging.INFO):
        async with module.lifespan(app):
            assert app.state.stock_eod_enabled is True
            if phase == "start":
                assert app.state.stock_eod_worker is None
                assert app.state.stock_eod_error_code
    assert "research_stop" in events and events[-1] == "engine_dispose"
    assert "RuntimeError" in caplog.text and "qa-secret" not in caplog.text


async def test_application_context_error_still_stops_workers_before_database_disposal(lifecycle):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = state.settings.stock_eod_sync_enabled = True
    state.settings.stock_research_jobs_enabled = True
    with pytest.raises(RuntimeError, match="application context failed"):
        async with module.lifespan(app):
            raise RuntimeError("application context failed")
    assert "eod_stop" in events and "research_stop" in events
    assert events.index("eod_stop") < events.index("engine_dispose")


async def test_base_schema_check_runs_before_workers_and_never_invokes_legacy_mutations(lifecycle):
    module, app, state, events, checker = lifecycle
    state.settings.stock_eod_enabled = state.settings.stock_eod_sync_enabled = True
    async with module.lifespan(app):
        state.schema_check.assert_awaited_once_with(app.state.engine)
        state.legacy_init.assert_not_awaited()
        assert events.index("base_schema_readiness") < events.index("eod_start")


async def test_base_schema_failure_disposes_engine_before_any_scheduler_or_redis_start(lifecycle, caplog):
    module, app, state, events, checker = lifecycle
    state.schema_error = RuntimeError("qa-private-migration-diagnostic")
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="qa-private-migration-diagnostic"):
        async with module.lifespan(app):
            pytest.fail("Incompatible database must not enter application lifespan")
    assert events == ["base_schema_readiness", "engine_dispose"]
    module.create_redis_connection.assert_not_awaited()
    module.start_scheduler.assert_not_awaited()
    state.legacy_init.assert_not_awaited()
    assert "qa-private-migration-diagnostic" not in caplog.text
