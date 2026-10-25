import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import insert, select, text


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_market
from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, normalize_eod
from app.services.stock_eod_sync import EodSourceError
from scripts.stock_eod_operator import AuditedEodSource, SyncReceipt, run_operator_sync


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    now = datetime.now(timezone.utc)
    original_catalog = [{"code": "600101", "name": "B18-old-fixture", "exchange": "SSE", "industry": "fixture", "list_date": date(2000, 1, 1)}]
    old_quote = {"ts_code": "600101.SH", "trade_date": "20261019", "open": 10, "high": 10, "low": 10, "close": 10, "pre_close": 10, "vol": 100, "amount": 100}
    old_snapshot = normalize_eod([old_quote], original_catalog, date(2026, 10, 19), acquired_at=now, has_more=False)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        tables = ("stock_instruments", "stock_eod_batches", "kline_history")
        public_counts = [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in tables]
        for table in tables:
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(text(f'CREATE SEQUENCE "{namespace}".stock_operator_test_ids START 100000'))
        await session.execute(text(f'ALTER TABLE "{namespace}".stock_instruments ALTER COLUMN id SET DEFAULT nextval(\'"{namespace}".stock_operator_test_ids\')'))
        await session.execute(insert(StockInstrument), [{"id": 93000, **original_catalog[0], "updated_at": now.replace(tzinfo=None)}])
        await session.execute(insert(KlineHistory), [{"id": 93000, "market_type": "stock", "code": "600101", "trade_date": date(2026, 10, 16), "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100}])
        session.add(StockEodBatch(trade_date=date(2026, 10, 19), status="completed", step="completed", request_id="b18-private", attempts=1, started_at=now, deadline_at=now + timedelta(seconds=60), finished_at=now, method_version=METHOD_VERSION, data_version=old_snapshot["data_version"], snapshot_json=old_snapshot))
        await session.commit()
    application.include_router(stock_market.router)
    application.state.stock_eod_enabled = True
    attempts = []
    failure = False
    cancel_started = asyncio.Event()

    async def source_handler(request):
        payload = json.loads(request.content)
        name = payload["api_name"]
        attempts.append(name)
        if failure:
            return httpx.Response(429, text="private-fixture-detail")
        if payload["params"].get("start_date") == "20261017":
            cancel_started.set()
            await asyncio.sleep(60)
        if name == "trade_cal":
            fields, rows = "exchange,cal_date,is_open", [["SSE", "20261020", 1]]
        elif name == "stock_basic":
            fields = "ts_code,symbol,name,industry,exchange,list_date,list_status"
            rows = [[code + ".SH", code, "B18-new-fixture", "fixture", "SSE", "20000101", "L"] for code in ("600101", "600102")]
        else:
            fields = "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount"
            rows = [[code + ".SH", "20261020", 10, 10, 10, 10, 10, 0, 123.45, 100] for code in ("600101", "600103")]
        return httpx.Response(200, json={"code": 0, "data": {"fields": fields.split(","), "items": rows, "has_more": False}})

    original = httpx.AsyncClient
    with patch("app.services.stock_eod_sync.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(source_handler), **kwargs)):
        receipt_path = ROOT / "logs" / f"b18-pg-operator-{uuid4().hex}.json"
        with SyncReceipt(receipt_path, date(2026, 10, 20), "qa-private-schema", now=now, logs_root=ROOT / "logs") as receipt:
            result = await run_operator_sync(scoped, AuditedEodSource("synthetic-not-secret", receipt), receipt)
            check("b18_real_PG_single_day_commit_and_three_budgeted_fixture_calls", result["status"] == "partially_completed" and result["source_calls"] == 3 and attempts == ["trade_cal", "stock_basic", "daily"])
            check("b18_receipt_contains_response_evidence_without_secrets", len(result["responses"]) == 3 and "synthetic-not-secret" not in receipt_path.read_text())
        async with scoped() as session:
            previous = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 19)))
            current = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 20)))
            check("b18_old_snapshot_unchanged_and_new_partial_honest", previous.snapshot_json == old_snapshot and current.status == "partially_completed" and current.snapshot_json["missing_codes"] == ["600102"] and current.snapshot_json["unknown_codes"] == ["600103"])
            check("b18_decimal_volume_and_legacy_history_preserved", current.snapshot_json["quotes"][0]["volume"] == 123.45 and await session.scalar(text("SELECT count(*) FROM kline_history")) == 1)
            check("b18_catalog_uses_private_sequence_only", await session.scalar(text("SELECT count(*) FROM stock_instruments")) == 2 and await session.scalar(text(f'SELECT last_value FROM "{namespace}".stock_operator_test_ids')) >= 100000)
        second_path = ROOT / "logs" / f"b18-pg-operator-replay-{uuid4().hex}.json"
        with SyncReceipt(second_path, date(2026, 10, 20), "qa-private-schema", now=now, logs_root=ROOT / "logs") as receipt:
            replayed = await run_operator_sync(scoped, AuditedEodSource("synthetic-not-secret", receipt), receipt)
            check("b18_same_day_new_receipt_cannot_repeat_source", replayed["status"] == "already_recorded" and replayed["source_calls"] == 0 and len(attempts) == 3)
        failure = True
        failed_path = ROOT / "logs" / f"b18-pg-operator-failed-{uuid4().hex}.json"
        try:
            with SyncReceipt(failed_path, date(2026, 10, 18), "qa-private-schema", now=now, logs_root=ROOT / "logs") as receipt:
                await run_operator_sync(scoped, AuditedEodSource("synthetic-not-secret", receipt), receipt)
            raise AssertionError("Source failure must propagate")
        except EodSourceError:
            failed_report = json.loads(failed_path.read_text())
            check("b18_source_failure_one_attempt_redacted_receipt", failed_report["status"] == "failed" and failed_report["source_calls"] == 1 and failed_report["error_code"] == "eod_source_rate_limited" and "private-fixture-detail" not in failed_path.read_text())
        async with scoped() as session:
            failed = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 18)))
            check("b18_source_failure_is_durable_not_silent_empty", failed.status == "failed" and failed.snapshot_json == {})
        failure = False
        cancel_path = ROOT / "logs" / f"b18-pg-operator-cancel-{uuid4().hex}.json"
        async def cancelled_operation():
            with SyncReceipt(cancel_path, date(2026, 10, 17), "qa-private-schema", now=now, logs_root=ROOT / "logs") as receipt:
                await run_operator_sync(scoped, AuditedEodSource("synthetic-not-secret", receipt), receipt)
        task = asyncio.create_task(cancelled_operation())
        await asyncio.wait_for(cancel_started.wait(), 5)
        task.cancel()
        try:
            await task
            raise AssertionError("Cancellation must propagate")
        except asyncio.CancelledError:
            cancelled = json.loads(cancel_path.read_text())
            check("b18_cancellation_preserves_durable_receipt", cancelled["status"] == "cancelled" and cancelled["source_calls"] == 1)
    path = "/api/stock/market/eod-gaps"
    check("b18_HTTP_unauthorized_and_futures_rejected", (await client.get(path)).status_code == 401 and (await client.get(path, headers=headers(owner_id))).status_code == 409)
    response = await client.get(path, headers=headers(stock_id))
    check("b18_HTTP_new_day_trace_and_real_gap_rows", response.status_code == 200 and response.json()["snapshot"]["trade_date"] == "2026-10-20" and response.json()["totals"] == {"missing": 1, "unknown": 1} and bool(response.headers.get("X-Request-ID")))
    async with scoped() as session:
        check("b18_public_source_counts_unchanged_by_private_tests", [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in tables] == public_counts)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b18"))
