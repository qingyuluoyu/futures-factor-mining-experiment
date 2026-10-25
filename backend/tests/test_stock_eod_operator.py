from datetime import date, datetime, timezone
import json
from pathlib import Path

import httpx
import pytest


def module():
    from scripts import stock_eod_operator

    return stock_eod_operator


def test_operator_receipt_is_exclusive_scoped_and_rejects_before_window_or_unbounded_dates(tmp_path):
    operator = module()
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    path = tmp_path / "receipt.json"
    with operator.SyncReceipt(path, date(2026, 10, 20), "local-console", now=now, logs_root=tmp_path) as receipt:
        assert json.loads(path.read_text())["source_calls"] == 0
        assert receipt.report["source_budget"] == 3
    with pytest.raises(FileExistsError):
        with operator.SyncReceipt(path, date(2026, 10, 20), "local-console", now=now, logs_root=tmp_path):
            pass
    for target, moment, destination, owner in [
        (date(2026, 10, 20), datetime(2026, 10, 20, 8, tzinfo=timezone.utc), tmp_path / "before.json", "local-console"),
        (date(2026, 10, 21), now, tmp_path / "future.json", "local-console"),
        (date(2026, 10, 1), now, tmp_path / "old.json", "local-console"),
        (date(2026, 10, 20), now.replace(tzinfo=None), tmp_path / "naive.json", "local-console"),
        (date(2026, 10, 20), now, tmp_path.parent / "outside.json", "local-console"),
        (date(2026, 10, 20), now, tmp_path / "invalid.json", "private-owner\n"),
    ]:
        with pytest.raises(ValueError):
            with operator.SyncReceipt(destination, target, owner, now=moment, logs_root=tmp_path):
                pass


def test_operator_attempts_are_durable_ordered_limited_and_evidence_allowlisted(tmp_path):
    operator = module()
    path = tmp_path / "receipt.json"
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    with operator.SyncReceipt(path, date(2026, 10, 20), "local-console", now=now, logs_root=tmp_path) as receipt:
        for name in ("trade_cal", "stock_basic", "daily"):
            receipt.begin_attempt(name)
            assert json.loads(path.read_text())["source_attempts"][-1] == name
            receipt.record_response({"api_name": name, "payload_sha256": "a" * 64, "byte_count": 100, "received_at": now.isoformat(), "row_count": 1, "api_key": "private-fixture"})
        with pytest.raises(ValueError):
            receipt.begin_attempt("daily")
        assert receipt.report["source_calls"] == 3
    assert "private-fixture" not in path.read_text()
    assert "api_key" not in path.read_text()


@pytest.mark.asyncio
async def test_actual_source_adapter_persists_before_network_and_closed_day_stops_at_one(tmp_path, monkeypatch):
    operator = module()
    path = tmp_path / "receipt.json"
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    requests = []

    async def handler(request):
        stored = json.loads(path.read_text())
        assert stored["source_calls"] == 1 and stored["source_attempts"] == ["trade_cal"]
        requests.append(request.url.path)
        return httpx.Response(200, json={"code": 0, "data": {"fields": ["exchange", "cal_date", "is_open"], "items": [["SSE", "20261018", 0]], "has_more": False}})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with operator.SyncReceipt(path, date(2026, 10, 18), "local-console", now=now, logs_root=tmp_path) as receipt:
        assert await operator.AuditedEodSource("synthetic-not-secret", receipt).fetch(date(2026, 10, 18)) is None
        assert len(receipt.report["responses"]) == 1
    assert requests == ["/debug/api/trade_cal"]


@pytest.mark.asyncio
async def test_source_error_is_recorded_redacted_without_retry(tmp_path, monkeypatch):
    operator = module()
    path = tmp_path / "receipt.json"
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        return httpx.Response(402, text="private-fixture-key-and-detail")

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ValueError):
        with operator.SyncReceipt(path, date(2026, 10, 20), "local-console", now=now, logs_root=tmp_path) as receipt:
            await operator.AuditedEodSource("synthetic-not-secret", receipt).fetch(date(2026, 10, 20))
    result = json.loads(path.read_text())
    assert result["status"] == "failed" and result["source_calls"] == 1
    assert result["error_code"] == "eod_source_payment_required"
    assert "private-fixture" not in path.read_text() and len(requests) == 1


@pytest.mark.asyncio
async def test_audit_disk_failure_stops_before_any_source_call(tmp_path, monkeypatch):
    operator = module()
    path = tmp_path / "receipt.json"
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    requests = []
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(lambda request: requests.append(request)), **kwargs))
    with operator.SyncReceipt(path, date(2026, 10, 20), "local-console", now=now, logs_root=tmp_path) as receipt:
        persist = receipt.persist
        def fail():
            raise OSError("private-disk-detail")
        receipt.persist = fail
        try:
            with pytest.raises(OSError):
                await operator.AuditedEodSource("synthetic-not-secret", receipt).fetch(date(2026, 10, 20))
            assert requests == []
        finally:
            receipt.persist = persist


def test_atomic_receipt_update_failure_preserves_previous_durable_json(tmp_path, monkeypatch):
    operator = module()
    path = tmp_path / "receipt.json"
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    with operator.SyncReceipt(path, date(2026, 10, 20), "local-console", now=now, logs_root=tmp_path) as receipt:
        before = path.read_text()
        original = operator.os.replace
        def fail(source, destination):
            raise OSError("private-replace-detail")
        monkeypatch.setattr(operator.os, "replace", fail)
        try:
            with pytest.raises(OSError):
                receipt.begin_attempt("trade_cal")
            assert path.read_text() == before
            assert json.loads(path.read_text())["source_calls"] == 0
        finally:
            monkeypatch.setattr(operator.os, "replace", original)
