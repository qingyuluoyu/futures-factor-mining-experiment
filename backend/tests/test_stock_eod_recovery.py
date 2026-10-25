from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, normalize_eod
from tests.test_stock_eod import catalog, row
from tests.test_stock_eod_integration import database


def fixture_snapshot():
    evidence = {"version": "teajoin-eod-response-v1", "mode": "live_http", "responses": [
        {"api_name": name, "payload_sha256": "a" * 64, "byte_count": 100, "row_count": count, "received_at": "2026-10-19T15:00:00+00:00"}
        for name, count in (("trade_cal", 1), ("stock_basic", 2), ("daily", 1))]}
    return normalize_eod([row(trade_date="20261019")], catalog(), date(2026, 10, 19), acquired_at=datetime(2026, 10, 19, 15, 1, tzinfo=timezone.utc), has_more=False, source_evidence=evidence)


@pytest.mark.asyncio
async def test_recovery_keeps_original_failure_audit_and_is_idempotent():
    from app.models.stock_eod_recovery import StockEodRecovery
    from app.services.stock_eod_recovery import recover_failed_eod

    async with database() as factory:
        async with factory() as session:
            StockEodRecovery.__table__.create(session.session.get_bind())
            for position, item in enumerate(catalog()):
                session.add(StockInstrument(id=position + 1, **item))
            moment = datetime(2026, 10, 19, 16, tzinfo=timezone.utc)
            batch = StockEodBatch(trade_date=date(2026, 10, 19), status="failed", step="failed", request_id=uuid4().hex, attempts=1,
                started_at=moment, deadline_at=moment + timedelta(minutes=2), finished_at=moment + timedelta(seconds=1), method_version=METHOD_VERSION,
                snapshot_json={}, error_code="eod_source_or_validation_failed", error_message="Original redacted failure")
            session.add(batch); await session.commit()
            batch_id = batch.id
        key = uuid4()
        async with factory() as session:
            first = await recover_failed_eod(session, snapshot=fixture_snapshot(), idempotency_key=key, operator_ref="local-console", reason="Controlled cached-source recovery")
            await session.commit()
        async with factory() as session:
            again = await recover_failed_eod(session, snapshot=fixture_snapshot(), idempotency_key=key, operator_ref="local-console", reason="Controlled cached-source recovery")
            assert again["replayed"] is True and first["recovery_id"] == again["recovery_id"]
            saved = await session.scalar(select(StockEodRecovery))
            restored = await session.scalar(select(StockEodBatch).where(StockEodBatch.id == batch_id))
            assert saved.before_row["status"] == "failed" and saved.before_row["error_message"] == "Original redacted failure"
            assert restored.status == "partially_completed" and restored.error_code == "eod_recovered"
            assert restored.snapshot_json["quotes"][0]["volume"] == 123.45
            assert restored.attempts == 1 and saved.source_calls == 0


@pytest.mark.parametrize("patch", [{"price_adjustment": "qfq"}, {"catalog_version": "b" * 64}, {"source_evidence": {}}, {"data_version": "c" * 64}])
def test_recovery_does_not_accept_changed_contract_or_unproven_source(patch):
    from app.services.stock_eod_recovery import validate_recovery_snapshot

    with pytest.raises(ValueError):
        validate_recovery_snapshot({**fixture_snapshot(), **patch}, catalog())
