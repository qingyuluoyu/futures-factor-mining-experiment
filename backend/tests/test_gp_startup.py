from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.factor_mining.startup import start_gp_worker
from scripts.migrate_gp_jobs import check_signature


@pytest.mark.asyncio
async def test_disabled_gp_startup_does_not_access_database_or_start_worker():
    application = SimpleNamespace(state=SimpleNamespace())
    def forbidden():
        raise AssertionError("disabled GP must not request DB")
    await start_gp_worker(application, forbidden, SimpleNamespace(gp_jobs_enabled=False))
    assert application.state.gp_checkpoint_worker is None


@pytest.mark.asyncio
async def test_mismatched_gp_schema_startup_is_check_only_and_stays_closed(monkeypatch):
    import app.services.factor_mining.startup as module
    checks = []
    async def check_only(connection, *, check):
        checks.append(check)
        raise RuntimeError("migration absent")
    class Session:
        async def connection(self):
            return object()
    @asynccontextmanager
    async def sessions():
        yield Session()
    monkeypatch.setattr(module, "migrate_gp_jobs", check_only)
    application = SimpleNamespace(state=SimpleNamespace())
    await start_gp_worker(application, sessions, SimpleNamespace(gp_jobs_enabled=True))
    assert checks == [True] and application.state.gp_checkpoint_worker is None


def test_float_column_check_signature_matches_postgres_without_weakening_bounds():
    expected = "runtime_seconds >= 0 AND runtime_seconds <= 1800"
    actual = "((runtime_seconds >= (0)::double precision) AND (runtime_seconds <= (1800)::double precision))"
    assert check_signature(actual) == check_signature(expected)
    assert check_signature(actual) != check_signature("runtime_seconds >= 0 AND runtime_seconds <= 1801")
