from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update

from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, normalize_eod
from tests.test_stock_eod import catalog, row
from tests.test_stock_eod_integration import database


def status_module():
    import app.services.stock_eod_status as module
    return module


NOW = datetime(2026, 10, 19, 0, 30, tzinfo=timezone(timedelta(hours=8)))


@pytest.mark.parametrize("changes,state", [
    ({"enabled": False}, "disabled"),
    ({"enabled": False, "error_code": "eod_migration_not_ready"}, "unavailable"),
    ({"sync_enabled": False}, "manual_only"),
    ({"worker_running": False}, "stopped"),
    ({}, "before_window"),
    ({"now": NOW.replace(hour=17)}, "pending"),
])
def test_sync_states_do_not_invent_today_prices_or_assume_trading_calendar(changes, state):
    values = {"now": NOW, "enabled": True, "sync_enabled": True, "worker_running": True, "today_job": None, "latest_snapshot": None, **changes}
    result = status_module().build_sync_status(**values)
    assert result["state"] == state
    assert result["collection_window"] == "16:30" and result["timezone"] == "Asia/Shanghai"
    assert result["today_job"] is None and result["latest_snapshot"] is None
    assert result["verification"] == "stored_metadata_only_not_independent_certification"
    assert "latest_trading_date" not in result and "verified" not in result


@pytest.mark.parametrize("stored,state", [("running", "running"), ("no_data", "closed"), ("completed", "completed"), ("partially_completed", "partially_completed"), ("failed", "failed")])
def test_stored_job_states_remain_distinct_and_error_details_are_not_returned(stored, state):
    job = {"id": uuid4(), "trade_date": NOW.date(), "status": stored, "step": "fetching" if stored == "running" else stored,
        "attempts": 1, "started_at": NOW - timedelta(minutes=1), "deadline_at": NOW + timedelta(minutes=1),
        "finished_at": None if stored == "running" else NOW, "error_code": "market_closed" if stored == "no_data" else None,
        "error_message": "private-provider-key"}
    result = status_module().build_sync_status(now=NOW, enabled=True, sync_enabled=True, worker_running=True, today_job=job, latest_snapshot=None)
    assert result["state"] == state
    assert "error_message" not in result["today_job"] and "private-provider-key" not in str(result)
    assert result["today_job"]["status"] == stored


def test_expired_running_job_is_visible_but_not_rewritten():
    job = {"id": uuid4(), "trade_date": NOW.date(), "status": "running", "step": "fetching", "attempts": 1,
        "started_at": NOW - timedelta(minutes=10), "deadline_at": NOW - timedelta(minutes=1), "finished_at": None, "error_code": None}
    result = status_module().build_sync_status(now=NOW, enabled=True, sync_enabled=True, worker_running=True, today_job=job, latest_snapshot=None)
    assert result["state"] == "expired" and job["status"] == "running" and job["finished_at"] is None


def test_status_queries_project_metadata_not_entire_price_snapshots():
    from sqlalchemy.dialects import postgresql
    statement = status_module().latest_status_statement(NOW.date())
    query = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "LIMIT 1" in query and "quotes" not in query
    assert "SELECT stock_eod_batches.snapshot_json," not in query
    assert "2026-10-19" in query


async def insert_snapshot(factory):
    snapshot = normalize_eod([row()], catalog(), date(2026, 10, 16), acquired_at=NOW.astimezone(timezone.utc), has_more=False)
    async with factory() as session:
        session.add(StockEodBatch(trade_date=date(2026, 10, 16), status="partially_completed", step="partially_completed", request_id=uuid4().hex,
            attempts=1, started_at=NOW, deadline_at=NOW + timedelta(minutes=2), finished_at=NOW, method_version=METHOD_VERSION,
            data_version=snapshot["data_version"], snapshot_json=snapshot))
        session.add(StockEodBatch(trade_date=date(2026, 10, 18), status="no_data", step="no_data", request_id=uuid4().hex,
            attempts=1, started_at=NOW, deadline_at=NOW + timedelta(minutes=2), finished_at=NOW, method_version=METHOD_VERSION,
            error_code="market_closed", error_message="secret-not-exposed", snapshot_json={}))
        await session.commit()
    return snapshot


@pytest.mark.asyncio
async def test_real_sql_latest_snapshot_is_not_called_today_or_independently_verified():
    async with database() as factory:
        snapshot = await insert_snapshot(factory)
        async with factory() as session:
            result = await status_module().read_sync_status(session, now=NOW, enabled=True, sync_enabled=True, worker_running=True)
        assert result["state"] == "before_window" and result["today_job"] is None
        assert result["latest_snapshot"]["trade_date"] == "2026-10-16"
        assert result["latest_snapshot"]["coverage"] == {"expected": 2, "quoted": 1, "missing": 1, "unknown": 0}
        assert result["latest_snapshot"]["raw_response_evidence"] is False
        assert result["latest_snapshot"]["data_version"] == snapshot["data_version"]
        async with factory() as session:
            saved = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 18)))
            assert saved.status == "no_data" and saved.attempts == 1 and saved.error_message == "secret-not-exposed"


@pytest.mark.asyncio
async def test_status_http_authentication_tracing_and_redacted_failure():
    from app.api.stock_market import router
    from app.core.deps import get_current_user, get_session
    application = FastAPI()
    application.include_router(router)
    application.state.stock_eod_enabled = True
    application.state.stock_eod_sync_enabled = True
    application.state.stock_eod_worker = SimpleNamespace(task=SimpleNamespace(done=lambda: False))
    class Broken:
        async def execute(self, *args):
            raise RuntimeError("private-database-token")
    application.dependency_overrides[get_session] = lambda: Broken()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        assert (await client.get("/api/stock/market/sync-status")).status_code == 401
        application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4(), market_type="stock")
        response = await client.get("/api/stock/market/sync-status")
        assert response.status_code == 503 and response.headers.get("X-Request-ID")
        assert response.headers.get("Cache-Control") == "no-store"
        assert "private-database-token" not in response.text


@pytest.mark.asyncio
async def test_disabled_status_never_reads_missing_tables():
    class NoDatabase:
        async def execute(self, *args):
            raise AssertionError("Disabled status cannot query tables")
    result = await status_module().read_sync_status(NoDatabase(), now=NOW, enabled=False, sync_enabled=False, worker_running=False)
    assert result["state"] == "disabled"


@pytest.mark.asyncio
async def test_authenticated_status_http_reads_real_sql_without_rewriting_jobs():
    from app.api.stock_market import router
    from app.core.deps import get_current_user, get_session
    application = FastAPI()
    application.include_router(router)
    application.state.stock_eod_enabled = True
    application.state.stock_eod_sync_enabled = True
    application.state.stock_eod_worker = SimpleNamespace(task=SimpleNamespace(done=lambda: False))
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4(), market_type="stock")
    async with database() as factory:
        snapshot = await insert_snapshot(factory)
        async def session_dependency():
            async with factory() as session:
                yield session
        application.dependency_overrides[get_session] = session_dependency
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
            response = await client.get("/api/stock/market/sync-status")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Request-ID"]
        payload = response.json()
        assert payload["sync_enabled"] is True
        assert payload["latest_snapshot"]["data_version"] == snapshot["data_version"]
        assert "quotes" not in response.text and "secret-not-exposed" not in response.text
        async with factory() as session:
            saved = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 18)))
            assert saved.status == "no_data" and saved.attempts == 1


@pytest.mark.asyncio
async def test_corrupt_snapshot_metadata_is_not_disguised_as_no_snapshot():
    async with database() as factory:
        snapshot = await insert_snapshot(factory)
        snapshot["covered_count"] = 9999
        async with factory() as session:
            await session.execute(update(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 16)).values(snapshot_json=snapshot))
            await session.commit()
        async with factory() as session:
            with pytest.raises(ValueError, match="覆盖元数据不一致"):
                await status_module().read_sync_status(session, now=NOW, enabled=True, sync_enabled=True, worker_running=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,api_name,metadata,expected", [
    ("cached_daily", "stock_basic", True, False),
    ("live_http", "daily", False, False),
    ("live_http", "stock_basic", True, False),
    ("live_http", "daily", True, True),
])
async def test_raw_evidence_requires_original_daily_response_not_just_catalog_or_cached_hash(mode, api_name, metadata, expected):
    async with database() as factory:
        snapshot = await insert_snapshot(factory)
        evidence = {"api_name": api_name, "payload_sha256": "a" * 64}
        if metadata:
            evidence.update(byte_count=427, received_at=NOW.isoformat(), row_count=snapshot["source_row_count"])
        snapshot["source_evidence"] = {"version": "teajoin-eod-response-v1", "mode": mode, "responses": [evidence]}
        async with factory() as session:
            await session.execute(update(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 16)).values(snapshot_json=snapshot))
            await session.commit()
        async with factory() as session:
            result = await status_module().read_sync_status(session, now=NOW, enabled=True, sync_enabled=True, worker_running=True)
        assert result["latest_snapshot"]["raw_response_evidence"] is expected
        assert result["verification"] == "stored_metadata_only_not_independent_certification"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_startup_publishes_only_the_public_sync_flag(monkeypatch, enabled):
    from unittest.mock import AsyncMock, Mock
    import app.main as module
    application = FastAPI()
    application.state.engine = object()
    monkeypatch.setattr(module, "migrate_stock_eod", AsyncMock())
    settings = SimpleNamespace(stock_eod_enabled=True, stock_eod_sync_enabled=enabled, tushare_token="")
    await module._start_stock_eod(application, settings, Mock())
    assert application.state.stock_eod_sync_enabled is enabled
    assert not hasattr(application.state, "settings")


@pytest.mark.parametrize("changes", [
    {"received_at": "invalid"}, {"received_at": "2026-10-19T00:30:00"}, {"received_at": None},
    {"byte_count": True}, {"byte_count": 0}, {"byte_count": 8 * 1024 * 1024 + 1},
    {"row_count": True}, {"row_count": 2}, {"row_count": None},
    {"payload_sha256": "invalid"}, {"payload_sha256": None},
])
def test_incomplete_or_invalid_daily_evidence_is_not_reported_as_present(changes):
    daily = {"api_name": "daily", "payload_sha256": "a" * 64, "byte_count": 427,
        "received_at": NOW.isoformat(), "row_count": 1, **changes}
    evidence = {"version": "teajoin-eod-response-v1", "mode": "live_http", "responses": [daily]}
    assert status_module()._has_raw_daily_evidence(evidence, 1) is False
