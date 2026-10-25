import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
import hashlib
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.paper import router as paper_router
from app.api.stock_factors import router as factors_router
from app.api.stock_market import router as market_router
from app.api.stock_replay import router as replay_router
from app.api.stock_research_jobs import router as jobs_router
from app.api.stock_research_reports import router as reports_router
from app.models.stock_eod import StockEodBatch
from app.models.stock import StockInstrument
from app.models.stock_eod_recovery import StockEodRecovery
from app.models.user import User
from app.services.stock_eod_sync import EodFetchResult, EodSourceError, StockEodWorker, catchup_dates
from app.services.stock_eod import normalize_eod
from app.services.stock_eod_recovery import recover_failed_eod
from scripts.migrate_stock_eod_recovery import migrate_stock_eod_recovery, MIGRATION_PATH, VERSION


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    for router in (paper_router, factors_router, market_router, replay_router, jobs_router, reports_router):
        application.include_router(router)
    paths = ["/api/stock/universe", "/api/stock/factors", "/api/stock/market/boards", "/api/stock/market/sync-status",
             "/api/stock/replay/accounts", "/api/stock/research/jobs",
             f"/api/stock/research/compare?left_id={uuid4()}&right_id={uuid4()}"]
    for mode in ("live", "virtual"):
        for path in paths:
            response = await client.get(path, headers=headers(owner_id, mode))
            check("b08_futures_stock_capability_409_before_business", response.status_code == 409 and response.headers.get("X-Market-Capability") == "stock_required" and bool(response.headers.get("X-Request-ID")))
    response = await client.get("/api/paper/account", headers=headers(stock_id))
    check("b08_stock_cannot_open_futures_paper_account", response.status_code == 409 and response.headers.get("X-Market-Capability") == "futures_required")
    for path in paths:
        check("b08_stock_routes_anonymous_401", (await client.get(path)).status_code == 401)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        check("b08_only_validated_private_namespace", namespace.startswith("qa_completion_b02_") and len(namespace) == len("qa_completion_b02_") + 32)
        for table in ("stock_eod_batches", "stock_instruments"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(text(f'CREATE SEQUENCE "{namespace}".qa_stock_instrument_id'))
        await session.execute(text(f"ALTER TABLE \"{namespace}\".stock_instruments ALTER COLUMN id SET DEFAULT nextval('\"{namespace}\".qa_stock_instrument_id')"))
        await session.execute(update(User).where(User.id == stock_id).values(status="frozen"))
        await session.commit()
    check("b08_frozen_stock_user_still_403", (await client.get("/api/stock/factors", headers=headers(stock_id))).status_code == 403)
    async with scoped() as session:
        await session.execute(update(User).where(User.id == stock_id).values(status="active"))
        await session.commit()
    check("b08_stock_user_retains_catalog_200", (await client.get("/api/stock/factors", headers=headers(stock_id))).status_code == 200)

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    dates = catchup_dates(now)
    class FixtureSource:
        def __init__(self):
            self.dates = []

        async def fetch(self, day, cached):
            self.dates.append(day)
            if day != dates[0]:
                return None
            catalog = [{"code": "600001", "name": "私有合成测试股", "exchange": "SSE", "industry": "测试行业", "list_date": date(2000, 1, 1)},
                       {"code": "600002", "name": "私有缺行情样本", "exchange": "SSE", "industry": "测试行业", "list_date": date(2000, 1, 1)}]
            rows = [{"ts_code": "600001.SH", "trade_date": day.strftime("%Y%m%d"), "open": 10, "high": 12, "low": 9, "close": 11, "pre_close": 10, "vol": 123.45, "amount": 456.789}]
            return EodFetchResult(catalog, rows, False, {"version": "synthetic-private-schema-v1", "mode": "fixture_not_vendor_certification"})

    source = FixtureSource()
    workers = [StockEodWorker(scoped, source, catchup_enabled=True), StockEodWorker(scoped, source, catchup_enabled=True)]
    outcomes = await asyncio.gather(*(worker.run_once(now=now) for worker in workers))
    check("b08_concurrent_workers_claim_one_date_once", len(source.dates) == 1 and sorted(item["status"] for item in outcomes) == ["already_recorded", "partially_completed"])
    async with scoped() as session:
        batch = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == dates[0]))
        original_version = batch.data_version
        check("b08_partial_prices_and_decimal_lot_preserved", batch.snapshot_json["quotes"][0]["volume"] == 123.45 and batch.snapshot_json["missing_codes"] == ["600002"])
    closed = await StockEodWorker(scoped, source, catchup_enabled=True).run_once(now=now)
    check("b08_restart_moves_to_next_unrecorded_date", closed["trade_date"] == dates[1].isoformat() and closed["status"] == "no_data")
    class FailingSource:
        calls = 0
        async def fetch(self, day, cached):
            self.calls += 1
            raise EodSourceError("synthetic failure")
    failing = FailingSource()
    try:
        await StockEodWorker(scoped, failing, catchup_enabled=True).run_once(now=now)
        raise AssertionError("Expected source failure")
    except EodSourceError:
        pass
    check("b08_source_failure_not_retried", failing.calls == 1)
    moment = datetime.now(timezone.utc)
    async with scoped() as session:
        session.add(StockEodBatch(trade_date=dates[-1], status="running", step="fetching", attempts=1, request_id=uuid4().hex,
            started_at=moment - timedelta(minutes=5), deadline_at=moment - timedelta(minutes=1), method_version="stock-eod-2026-10-18.v1", snapshot_json={}))
        await session.commit()
    for _ in range(4):
        await workers[0].run_once(now=now)
    check("b08_window_exhausted_without_unbounded_download", (await workers[0].run_once(now=now))["status"] == "caught_up")
    async with scoped() as session:
        failed = (await session.execute(select(StockEodBatch).where(StockEodBatch.status == "failed"))).scalars().all()
        check("b08_source_failed_and_expired_both_retained", len(failed) == 2 and any(item.error_code == "eod_execution_expired" for item in failed))
        check("b08_exact_seven_dates_no_future_or_window_escape", await session.scalar(select(func.count()).select_from(StockEodBatch)) == 7 and all(day in dates for day in source.dates))
        check("b08_original_valid_batch_not_rewritten", (await session.scalar(select(StockEodBatch.data_version).where(StockEodBatch.trade_date == dates[0]))) == original_version)
    application.state.stock_eod_enabled = True
    application.state.stock_eod_sync_enabled = True
    response = await client.get("/api/stock/market/sync-status", headers=headers(stock_id))
    check("b08_sync_status_real_pg_http_and_trace", response.status_code == 200 and response.headers.get("Cache-Control") == "no-store" and bool(response.headers.get("X-Request-ID")))
    payload = response.json()
    check("b08_status_reports_failures_without_claiming_calendar_certification", len(payload["catchup"]["failed_dates"]) == 2 and payload["catchup"]["calendar_verified"] is False and payload["catchup"]["pending_dates"] == [])
    check("b08_latest_fixture_metadata_not_raw_prices_or_secret", "quotes" not in response.text and payload["latest_snapshot"]["raw_response_evidence"] is False)

    async with scoped() as session:
        await session.execute(text(f'CREATE TABLE "{namespace}".stock_eod_schema_versions (LIKE public.stock_eod_schema_versions INCLUDING ALL)'))
        checksum = hashlib.sha256((ROOT / "backend/migrations/20261018_003_stock_eod.sql").read_bytes()).hexdigest()
        await session.execute(text("INSERT INTO stock_eod_schema_versions(version,checksum) VALUES ('20261018_003_stock_eod',:checksum)"), {"checksum": checksum})
        await session.commit()
    async with scoped() as session:
        try:
            await migrate_stock_eod_recovery(await session.connection(), check=True)
            raise AssertionError("Expected migration missing")
        except RuntimeError:
            check("b08_recovery_check_does_not_silently_create_schema", await session.scalar(text("SELECT to_regclass('stock_eod_recoveries')")) is None)
    async with scoped() as session:
        check("b08_recovery_expand_migration_applied", (await migrate_stock_eod_recovery(await session.connection()))["applied"] is True)
        await session.commit()
    async with scoped() as session:
        check("b08_recovery_migration_repeat_and_check", (await migrate_stock_eod_recovery(await session.connection()))["applied"] is False and (await migrate_stock_eod_recovery(await session.connection(), check=True))["checked"] is True)
        instruments = [dict(item) for item in (await session.execute(select(StockInstrument.code, StockInstrument.name, StockInstrument.exchange, StockInstrument.industry, StockInstrument.list_date).order_by(StockInstrument.code))).mappings()]
    stamp = datetime.now(timezone.utc)
    evidence = {"version": "teajoin-eod-response-v1", "mode": "live_http", "responses": [
        {"api_name": name, "payload_sha256": "a" * 64, "byte_count": 100, "row_count": count, "received_at": stamp.isoformat()}
        for name, count in (("trade_cal", 1), ("stock_basic", 2), ("daily", 1))]}
    recovery_snapshot = normalize_eod([{ "ts_code": "600001.SH", "trade_date": dates[2].strftime("%Y%m%d"), "open": 10, "high": 12, "low": 9, "close": 11, "pre_close": 10, "vol": 123.45, "amount": 456.789}], instruments, dates[2], acquired_at=stamp, has_more=False, source_evidence=evidence)
    command = {"snapshot": recovery_snapshot, "idempotency_key": uuid4(), "operator_ref": "isolated-fixture", "reason": "Synthetic protocol evidence, not vendor certification"}
    try:
        async with scoped() as session:
            await recover_failed_eod(session, **command)
            raise RuntimeError("intentional transaction interruption")
    except RuntimeError:
        pass
    async with scoped() as session:
        check("b08_recovery_interruption_rolls_back_projection_and_audit", await session.scalar(select(func.count()).select_from(StockEodRecovery)) == 0 and (await session.scalar(select(StockEodBatch.status).where(StockEodBatch.trade_date == dates[2]))) == "failed")
    async def recover_once():
        async with scoped() as session:
            result = await recover_failed_eod(session, **command)
            await session.commit()
            return result
    recovered = await asyncio.gather(recover_once(), recover_once())
    check("b08_concurrent_cached_recovery_is_idempotent", sorted(item["replayed"] for item in recovered) == [False, True] and recovered[0]["recovery_id"] == recovered[1]["recovery_id"])
    async with scoped() as session:
        audit = await session.scalar(select(StockEodRecovery))
        repaired = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == dates[2]))
        check("b08_original_failure_is_immutable_audit_not_erased", audit.before_row["status"] == "failed" and audit.before_row["error_code"] == "eod_source_or_validation_failed" and audit.source_calls == 0 and repaired.error_code == "eod_recovered")
        try:
            await recover_failed_eod(session, **{**command, "reason": "different intent"})
            raise AssertionError("Expected idempotency conflict")
        except ValueError:
            check("b08_recovery_changed_intent_rejected", await session.scalar(select(func.count()).select_from(StockEodRecovery)) == 1)
    async with scoped() as session:
        await session.execute(text("UPDATE stock_eod_schema_versions SET checksum=:checksum WHERE version=:version"), {"checksum": "0" * 64, "version": VERSION})
        try:
            await migrate_stock_eod_recovery(await session.connection(), check=True)
            raise AssertionError("Expected checksum mismatch")
        except RuntimeError:
            check("b08_recovery_changed_migration_checksum_rejected", True)
        await session.execute(text("UPDATE stock_eod_schema_versions SET checksum=:checksum WHERE version=:version"), {"checksum": hashlib.sha256(MIGRATION_PATH.read_bytes()).hexdigest(), "version": VERSION})
        await session.commit()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b08"))
