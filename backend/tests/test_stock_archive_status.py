from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


NOW = datetime(2026, 10, 19, 5, 15, tzinfo=timezone(timedelta(hours=8)))


def module():
    import app.services.stock_archive_status as service
    return service


@pytest.mark.parametrize("options,state", [({"enabled": False}, "disabled"), ({"ready": False}, "unavailable"),
    ({"worker_running": False}, "stopped"), ({}, "idle")])
def test_archive_service_states_do_not_imply_historical_migration(options, state):
    result = module().build_archive_status(
        **{"now": NOW, "enabled": True, "ready": True, "worker_running": True, "latest_job": None, **options})
    assert result["state"] == state and result["scope"] == "shadow_only_user_read_path_unchanged"
    assert result["source_calls"] == result["model_calls"] == 0


@pytest.mark.parametrize("status,state", [("running", "running"), ("completed", "completed"), ("failed", "failed"), ("cancelled", "cancelled")])
def test_archive_job_status_and_error_redaction(status, state):
    job = {"id": uuid4(), "request_id": "b" * 32, "source_batch_id": uuid4(), "source_data_version": "a" * 64,
        "trade_date": NOW.date(), "status": status, "step": "archiving" if status == "running" else status,
        "attempts": 1, "started_at": NOW, "updated_at": NOW, "deadline_at": NOW + timedelta(seconds=90),
        "finished_at": None if status == "running" else NOW, "archive_batch_id": uuid4() if status == "completed" else None,
        "error_code": "archive_cancelled" if status == "cancelled" else None, "error_message": "private-database-token"}
    result = module().build_archive_status(now=NOW, enabled=True, ready=True, worker_running=True, latest_job=job)
    assert result["state"] == state and "error_message" not in result["latest_job"] and "private-database-token" not in str(result)
    if status == "running":
        job["deadline_at"] = NOW - timedelta(seconds=1)
        assert module().build_archive_status(now=NOW, enabled=True, ready=True, worker_running=True, latest_job=job)["state"] == "expired"
        assert job["status"] == "running"


@pytest.mark.asyncio
async def test_disabled_or_unready_archive_does_not_query_missing_tables():
    class Forbidden:
        async def execute(self, *args):
            raise AssertionError("No query before readiness")
    for enabled, ready in ((False, False), (True, False)):
        result = await module().read_archive_status(Forbidden(), now=NOW, enabled=enabled, ready=ready, worker_running=False)
        assert result["latest_job"] is None


def test_archive_status_query_is_single_row_projection_without_source_prices_or_private_error():
    from sqlalchemy.dialects import postgresql
    sql = str(module().status_statement().compile(dialect=postgresql.dialect()))
    assert "error_message" not in sql and "snapshot_json" not in sql and "LIMIT" in sql
