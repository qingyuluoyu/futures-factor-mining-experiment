from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError


NOW = datetime(2026, 10, 19, 7, 10, tzinfo=timezone(timedelta(hours=8)))


def request(**changes):
    from app.schemas.stock_archive_recovery import RecoveryRequest
    return RecoveryRequest.model_validate({"source_job_id": str(uuid4()), "expected_data_version": "a" * 64,
        "idempotency_key": str(uuid4()), "reason": "经核查存储暂时故障已恢复", **changes})


@pytest.mark.parametrize("changes", [{"source_job_id": "invalid"}, {"expected_data_version": "A" * 64},
    {"idempotency_key": None}, {"reason": "  "}, {"reason": "短理由"}, {"reason": "字" * 161},
    {"reason": 123456789}, {"source_url": "https://untrusted.invalid"}])
def test_recovery_request_is_typed_bounded_and_cannot_choose_external_tools(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_recovery_request_hash_is_stable_but_different_reason_is_not_same_request():
    from app.services.stock_archive_recovery import request_hash
    body = request()
    assert request_hash(body) == request_hash(body.model_copy())
    assert request_hash(body) != request_hash(body.model_copy(update={"reason": "核查完成后由管理员重新恢复"}))
    assert len(request_hash(body)) == 64


def test_recovery_report_never_returns_reason_or_private_failure():
    from app.services.stock_archive_recovery import recovery_report
    record = {"id": uuid4(), "source_job_id": uuid4(), "source_batch_id": uuid4(), "source_data_version": "a" * 64,
        "status": "running", "deadline_at": NOW - timedelta(seconds=1), "attempt_number": 1,
        "archive_batch_id": None, "request_id": "b" * 32, "error_code": None,
        "reason": "private-reason", "error_message": "private-db-token"}
    result = recovery_report(record, now=NOW)
    assert result["status"] == "expired" and record["status"] == "running"
    assert "private" not in str(result) and "reason" not in result and "error_message" not in result
    assert result["source_calls"] == result["model_calls"] == 0


@pytest.mark.asyncio
async def test_missing_operator_fails_before_any_business_resource_read():
    from app.services.stock_archive_recovery import RecoveryError, inspect_recovery
    class NoAdmin:
        async def scalar(self, statement):
            assert "users" in str(statement)
            return None
    with pytest.raises(RecoveryError) as raised:
        await inspect_recovery(NoAdmin(), request(), operator_id=uuid4(), now=NOW)
    assert raised.value.code == "admin_required" and raised.value.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("private-storage-token")])
async def test_cancelled_claim_exit_propagates_even_if_failure_cannot_be_saved(monkeypatch, failure):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services import stock_archive_recovery as module
    job = {"id": uuid4(), "request_id": uuid4().hex}
    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(commit=AsyncMock())
        raise asyncio.CancelledError()
    monkeypatch.setattr(module, "_limits", AsyncMock())
    monkeypatch.setattr(module, "_claim", AsyncMock(return_value=(job, True, None)))
    monkeypatch.setattr(module, "_save_failure", AsyncMock(side_effect=failure))
    with pytest.raises(asyncio.CancelledError):
        await module.run_recovery(factory, request(), operator_id=uuid4(), now=NOW)


def cli_module():
    import importlib.util
    from pathlib import Path
    specification = importlib.util.spec_from_file_location("archive_recovery_cli_test", Path(__file__).resolve().parents[2] / "scripts/recover-stock-archive.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_cli_without_access_token_fails_before_settings_or_database(monkeypatch):
    from types import SimpleNamespace
    module = cli_module()
    monkeypatch.delenv("STOCK_ARCHIVE_RECOVERY_TOKEN", raising=False)
    monkeypatch.setattr(module, "load_runtime_settings", lambda: pytest.fail("No auth must not load runtime settings or connect"))
    with pytest.raises(module.RecoveryError) as raised:
        await module.run(SimpleNamespace())
    assert raised.value.code == "access_token_required" and raised.value.status == 401


def test_cli_requires_explicit_apply_and_does_not_accept_operator_id_or_token_argument():
    module = cli_module()
    arguments = ["--source-job-id", str(uuid4()), "--expected-data-version", "a" * 64,
        "--idempotency-key", str(uuid4()), "--reason", "核查确认后人工恢复归档"]
    assert module.parser().parse_args(arguments).apply is False
    assert module.parser().parse_args(arguments + ["--apply"]).apply is True
    for invalid in (["--operator-id", str(uuid4())], ["--token", "not-a-token"]):
        with pytest.raises(SystemExit):
            module.parser().parse_args(arguments + invalid)
