import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_archive_job import StockArchiveJob
from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.models.stock_eod import StockEodBatch
from app.services import stock_archive_worker as worker
from app.services.stock_archive_status import read_archive_status
from scripts.migrate_stock_archive_jobs import migrate_stock_archive_jobs
from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
from scripts.migrate_stock_eod import migrate_stock_eod
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings
from tests.test_stock_archive_worker import NOW
from tests.test_stock_daily_archive import rehash, source_batch


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 is permitted")
    namespace = "qa_archive_worker_" + uuid4().hex
    if not re.fullmatch(r"qa_archive_worker_[a-f0-9]{32}", namespace):
        raise RuntimeError("Invalid owned namespace")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    output = ROOT / "logs" / f"stock-archive-worker-pg-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{namespace[-8:]}.json"
    checks = []
    report = {"checks": checks, "namespace": namespace, "source_calls": 0, "model_calls": 0, "cleanup": False}

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    class ScopedEngine:
        @asynccontextmanager
        async def begin(self):
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{namespace}"'))
                yield connection

    @asynccontextmanager
    async def scoped():
        async with factory() as session, session.begin():
            await session.execute(text(f'SET LOCAL search_path TO "{namespace}"'))
            yield session

    async def source(day, *, corrupt=False):
        value = source_batch()
        value.trade_date = NOW.date().replace(day=day)
        value.snapshot_json["trade_date"] = value.trade_date.isoformat()
        value.snapshot_json["quotes"][0]["trade_date"] = value.trade_date.isoformat()
        rehash(value)
        value.data_version = "b" * 64 if corrupt else value.data_version
        async with scoped() as session:
            session.add(StockEodBatch(id=value.id, trade_date=value.trade_date, status=value.status, step=value.status,
                request_id=uuid4().hex, attempts=1, started_at=value.finished_at, deadline_at=value.finished_at + timedelta(minutes=2),
                finished_at=value.finished_at, method_version=value.method_version, data_version=value.data_version, snapshot_json=value.snapshot_json))
        return value

    async def fingerprint():
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            result = {}
            result["history"] = [dict(item) for item in (await connection.execute(text("SELECT market_type, count(*) AS rows, sum(id) AS ids, sum(volume::numeric) AS volume, sum(close::numeric) AS prices FROM public.kline_history GROUP BY market_type ORDER BY market_type"))).mappings()]
            for table, ordering in (("stock_eod_batches", "trade_date"), ("stock_instruments", "code")):
                result[table] = dict((await connection.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY {ordering})) AS digest FROM public.{table} AS item"))).mappings().one())
            result["archive"] = dict((await connection.execute(text("SELECT count(*) AS rows, sum(volume) AS lots, sum(close) AS prices FROM public.stock_daily_observations"))).mappings().one())
            return result

    original_archive = worker.archive_eod
    before = await fingerprint()
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
        for migration in (migrate_stock_eod, migrate_stock_daily_archive, migrate_stock_archive_jobs):
            first = await migration(ScopedEngine())
            checked = await migration(ScopedEngine(), check=True)
            check(f"{first['version']}_applied_and_checked", first["applied"] and checked["checked"] and not checked["applied"])
        healthy = await source(10)
        concurrent = await asyncio.wait_for(asyncio.gather(*(worker.process_archive_once(scoped, now=NOW) for _ in range(6))), 25)
        check("six_worker_claims_execute_one_archive", sum(item["status"] == "completed" for item in concurrent) == 1
            and sum(item["status"] == "idle" for item in concurrent) == 5)
        async with scoped() as session:
            job = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.source_batch_id == healthy.id))
            check("job_and_decimal_observation_commit_together", job.status == "completed" and job.archive_batch_id is not None
                and await session.scalar(select(func.count()).select_from(StockDailyObservation).where(StockDailyObservation.batch_id == job.archive_batch_id)) == 1)
            check("trace_and_one_attempt_persisted", len(job.request_id) == 32 and job.attempts == 1 and job.finished_at is not None)
        check("completed_job_not_reprocessed", (await worker.process_archive_once(scoped, now=NOW))["status"] == "idle")
        broken = await source(11, corrupt=True)
        failed = await worker.process_archive_once(scoped, now=NOW)
        async with scoped() as session:
            job = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.source_batch_id == broken.id))
            check("invalid_snapshot_is_visible_failed_not_empty_success", failed["status"] == "failed" and job.status == "failed"
                and job.error_code == "archive_validation_or_storage_failed" and job.archive_batch_id is None)
        check("deterministic_failure_never_automatically_retries", (await worker.process_archive_once(scoped, now=NOW))["status"] == "idle")
        atomic = await source(12)
        async def force_database_failure(session, value, *, now):
            await original_archive(session, value, now=now)
            await session.execute(text("SELECT 1/0"))
        worker.archive_eod = force_database_failure
        try:
            result = await worker.process_archive_once(scoped, now=NOW)
        finally:
            worker.archive_eod = original_archive
        async with scoped() as session:
            check("archive_failure_rolls_back_whole_batch_then_persists_failed_job", result["status"] == "failed"
                and await session.scalar(select(func.count()).select_from(StockDailyArchiveBatch).where(StockDailyArchiveBatch.source_batch_id == atomic.id)) == 0
                and (await session.scalar(select(StockArchiveJob.status).where(StockArchiveJob.source_batch_id == atomic.id))) == "failed")
        stale = await source(13)
        values = worker.new_job_values({"id": stale.id, "trade_date": stale.trade_date, "data_version": stale.data_version}, now=NOW - timedelta(minutes=10))
        async with scoped() as session:
            session.add(StockArchiveJob(**values))
        result = await worker.process_archive_once(scoped, now=NOW)
        async with scoped() as session:
            job = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.source_batch_id == stale.id))
            check("expired_claim_is_failed_without_reentering_archive", result["status"] == "idle" and job.status == "failed" and job.error_code == "archive_expired")
        cancelled = await source(14)
        entered = asyncio.Event()
        async def hold_archive(session, value, *, now):
            await original_archive(session, value, now=now)
            entered.set()
            await asyncio.Event().wait()
        worker.archive_eod = hold_archive
        task = asyncio.create_task(worker.process_archive_once(scoped, now=NOW))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Cancellation must propagate")
        finally:
            worker.archive_eod = original_archive
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with scoped() as session:
            job = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.source_batch_id == cancelled.id))
            check("cancelled_job_visible_and_partial_archive_rolled_back", job.status == "cancelled" and job.error_code == "archive_cancelled"
                and await session.scalar(select(func.count()).select_from(StockDailyArchiveBatch).where(StockDailyArchiveBatch.source_batch_id == cancelled.id)) == 0)
        committed_cancel = await source(15)
        first_exit = True
        @asynccontextmanager
        async def cancel_after_commit():
            nonlocal first_exit
            interrupt = first_exit
            first_exit = False
            async with scoped() as session:
                yield session
            if interrupt:
                raise asyncio.CancelledError()
        try:
            await worker.process_archive_once(cancel_after_commit, now=NOW)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Claim-exit cancellation must propagate")
        async with scoped() as session:
            job = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.source_batch_id == committed_cancel.id))
            check("cancel_after_committed_claim_records_real_cancelled", job.status == "cancelled" and job.error_code == "archive_cancelled")
        uncommitted = await source(16)
        first_commit = True
        class BeforeCommit:
            def __init__(self, session):
                self.session = session
            def __getattr__(self, name):
                return getattr(self.session, name)
            async def commit(self):
                raise asyncio.CancelledError()
        @asynccontextmanager
        async def cancel_before_commit():
            nonlocal first_commit
            interrupt = first_commit
            first_commit = False
            async with scoped() as session:
                yield BeforeCommit(session) if interrupt else session
        try:
            await worker.process_archive_once(cancel_before_commit, now=NOW)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Uncommitted cancellation must propagate")
        async with scoped() as session:
            check("uncommitted_claim_does_not_fabricate_cancelled_job", await session.scalar(select(func.count()).select_from(StockArchiveJob)
                .where(StockArchiveJob.source_batch_id == uncommitted.id)) == 0)
        check("uncommitted_source_can_be_claimed_on_next_tick", (await worker.process_archive_once(scoped, now=NOW))["status"] == "completed")
        completed_cancel = await source(9)
        exits = 0
        @asynccontextmanager
        async def cancel_after_completion_commit():
            nonlocal exits
            exits += 1
            interrupt = exits == 2
            async with scoped() as session:
                yield session
            if interrupt:
                raise asyncio.CancelledError()
        try:
            await worker.process_archive_once(cancel_after_completion_commit, now=NOW)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Post-completion cancellation must propagate")
        async with scoped() as session:
            job = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.source_batch_id == completed_cancel.id))
            check("cancellation_cannot_overwrite_already_committed_completion", job.status == "completed" and job.error_code is None
                and await session.scalar(select(func.count()).select_from(StockDailyObservation).where(StockDailyObservation.batch_id == job.archive_batch_id)) == 1)
        await source(19)
        check("before_close_today_source_is_not_claimed", (await worker.process_archive_once(scoped, now=NOW))["status"] == "idle")
        async with scoped() as session:
            status = await read_archive_status(session, now=NOW, enabled=True, ready=True, worker_running=True)
            check("readonly_status_has_no_prices_or_private_failure", status["latest_job"] is not None
                and "error_message" not in status["latest_job"] and status["scope"] == "shadow_only_user_read_path_unchanged")
            sources = (await session.execute(select(StockEodBatch))).scalars().all()
            check("archive_never_rewrites_source_eod_status", len(sources) == 9 and all(value.status == "partially_completed" for value in sources))
        check("public_stock_futures_eod_catalog_archive_aggregates_unchanged", before == await fingerprint())
    finally:
        worker.archive_eod = original_archive
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
        async with engine.connect() as connection:
            report["cleanup"] = await connection.scalar(text("SELECT to_regnamespace(:namespace)"), {"namespace": namespace}) is None
        check("owned_private_schema_removed", report["cleanup"])
        report["public_aggregates_unchanged_after_cleanup"] = before == await fingerprint()
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print("report", output.name, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
