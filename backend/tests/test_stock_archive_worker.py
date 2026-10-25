import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


NOW = datetime(2026, 10, 19, 5, 15, tzinfo=timezone(timedelta(hours=8)))


def worker_module():
    import app.services.stock_archive_worker as module
    return module


def test_candidate_query_is_bounded_metadata_only_and_excludes_unclosed_today():
    from sqlalchemy.dialects import postgresql
    module = worker_module()
    sql = str(module.eligibility_statement(NOW).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "LIMIT 1" in sql and "snapshot_json" not in sql and "NOT (EXISTS" in sql
    assert "2026-10-18" in sql and "stock_archive_jobs" in sql
    after = str(module.eligibility_statement(NOW.replace(hour=17)).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "2026-10-19" in after
    with pytest.raises(ValueError):
        module.eligibility_statement(NOW.replace(tzinfo=None))


def test_new_job_has_trace_version_one_attempt_and_bounded_deadline():
    module = worker_module()
    source = {"id": uuid4(), "trade_date": date(2026, 10, 16), "data_version": "a" * 64}
    values = module.new_job_values(source, now=NOW)
    assert values["source_batch_id"] == source["id"] and values["source_data_version"] == source["data_version"]
    assert values["status"] == "running" and values["step"] == "archiving" and values["attempts"] == 1
    assert values["deadline_at"] - values["started_at"] == timedelta(seconds=90)
    assert values["updated_at"] == values["started_at"] and len(values["request_id"]) == 32
    assert values["finished_at"] is None and values["archive_batch_id"] is None


@pytest.mark.parametrize("changes", [{"id": "not-uuid"}, {"data_version": "invalid"}, {"trade_date": date(2026, 10, 20)}])
def test_invalid_candidate_cannot_claim_a_job(changes):
    source = {"id": uuid4(), "trade_date": date(2026, 10, 16), "data_version": "a" * 64, **changes}
    with pytest.raises(ValueError):
        worker_module().new_job_values(source, now=NOW)


@pytest.mark.asyncio
async def test_worker_runs_one_bounded_tick_then_sleeps_without_upstream_or_tight_loop(monkeypatch):
    module = worker_module()
    called, sleeping = asyncio.Event(), asyncio.Event()
    calls = []
    async def process(factory):
        calls.append(factory)
        called.set()
        return {"status": "idle"}
    async def sleep(seconds):
        assert seconds == 300
        sleeping.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(module, "process_archive_once", process)
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    worker = module.StockArchiveWorker("bounded-factory")
    worker.start()
    await asyncio.wait_for(called.wait(), 1)
    await asyncio.wait_for(sleeping.wait(), 1)
    await worker.stop()
    assert calls == ["bounded-factory"] and worker.task.done()


@pytest.mark.asyncio
async def test_archive_startup_disabled_never_queries_migrations_or_creates_worker(monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from fastapi import FastAPI
    import app.main as module
    migration = AsyncMock(side_effect=AssertionError("Disabled archive cannot touch DB"))
    monkeypatch.setattr(module, "migrate_stock_archive_jobs", migration)
    app = FastAPI()
    app.state.stock_eod_enabled = True
    await module._start_stock_archive(app, SimpleNamespace(stock_daily_archive_enabled=False), Mock())
    migration.assert_not_awaited()
    assert app.state.stock_archive_enabled is False and app.state.stock_archive_worker is None
    assert app.state.stock_eod_enabled is True and not hasattr(app.state, "settings")


@pytest.mark.asyncio
async def test_archive_migration_failure_is_visible_without_disabling_original_eod(monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from fastapi import FastAPI
    import app.main as module
    monkeypatch.setattr(module, "migrate_stock_eod", AsyncMock())
    monkeypatch.setattr(module, "migrate_stock_daily_archive", AsyncMock())
    monkeypatch.setattr(module, "migrate_stock_archive_jobs", AsyncMock(side_effect=RuntimeError("private-db-url")))
    app = FastAPI()
    app.state.engine = object()
    app.state.stock_eod_enabled = True
    await module._start_stock_archive(app, SimpleNamespace(stock_daily_archive_enabled=True), Mock())
    assert app.state.stock_archive_ready is False and app.state.stock_archive_error_code == "archive_migration_not_ready"
    assert app.state.stock_archive_worker is None and app.state.stock_eod_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("start_fails", [False, True])
async def test_archive_startup_checks_all_versions_before_starting_and_cleans_up_failure(monkeypatch, start_fails):
    from unittest.mock import AsyncMock, Mock
    from fastapi import FastAPI
    import app.main as module
    migrations = [AsyncMock() for _ in range(3)]
    for name, migration in zip(("migrate_stock_eod", "migrate_stock_daily_archive", "migrate_stock_archive_jobs"), migrations):
        monkeypatch.setattr(module, name, migration)
    instance = SimpleNamespace(start=Mock(side_effect=RuntimeError("private-worker-token") if start_fails else None), stop=AsyncMock())
    monkeypatch.setattr(module, "StockArchiveWorker", lambda factory: instance)
    app = FastAPI()
    app.state.engine, app.state.session_factory = object(), object()
    await module._start_stock_archive(app, SimpleNamespace(stock_daily_archive_enabled=True), Mock())
    for migration in migrations:
        migration.assert_awaited_once_with(app.state.engine, check=True)
    if start_fails:
        instance.stop.assert_awaited_once()
        assert app.state.stock_archive_worker is None and app.state.stock_archive_error_code == "archive_worker_start_failed"
    else:
        assert app.state.stock_archive_ready is True and app.state.stock_archive_worker is instance


def test_archive_flag_is_off_by_default_without_loading_private_environment():
    from app.core.config import Settings
    assert Settings.model_fields["stock_daily_archive_enabled"].default is False


@pytest.mark.asyncio
async def test_cancellation_after_claim_commit_still_records_cancelled(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    module = worker_module()
    values = module.new_job_values({"id": uuid4(), "trade_date": date(2026, 10, 16), "data_version": "a" * 64}, now=NOW)
    session = SimpleNamespace(commit=AsyncMock())
    @asynccontextmanager
    async def factory():
        yield session
        raise asyncio.CancelledError()
    monkeypatch.setattr(module, "_limits", AsyncMock())
    monkeypatch.setattr(module, "_claim", AsyncMock(return_value=values))
    saved = AsyncMock(return_value=True)
    monkeypatch.setattr(module, "_save_failure", saved)
    with pytest.raises(asyncio.CancelledError):
        await module.process_archive_once(factory, now=NOW)
    session.commit.assert_awaited_once()
    saved.assert_awaited_once_with(factory, values, cancelled=True, now=NOW.astimezone(timezone.utc))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("private-db-token")])
async def test_cancellation_still_propagates_when_terminal_state_cannot_be_saved(monkeypatch, failure):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    module = worker_module()
    values = module.new_job_values({"id": uuid4(), "trade_date": date(2026, 10, 16), "data_version": "a" * 64}, now=NOW)
    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(commit=AsyncMock())
        raise asyncio.CancelledError()
    monkeypatch.setattr(module, "_limits", AsyncMock())
    monkeypatch.setattr(module, "_claim", AsyncMock(return_value=values))
    monkeypatch.setattr(module, "_save_failure", AsyncMock(side_effect=failure))
    with pytest.raises(asyncio.CancelledError):
        await module.process_archive_once(factory, now=NOW)
