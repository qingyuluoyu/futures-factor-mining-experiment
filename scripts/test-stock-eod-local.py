import asyncio
import hashlib
import json
import re
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, eod_bar_statement, eod_bars, latest_eod, normalize_eod
from app.services.stock_eod_sync import EodSourceError, run_eod
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings

TABLES = ("stock_eod_batches", "stock_instruments")
COMPLETE_DATE = date(2024, 1, 2)
PARTIAL_DATE = date(2024, 1, 3)
FAILED_DATE = date(2024, 1, 4)
EXPIRED_DATE = date(2024, 1, 5)
ATOMIC_DATE = date(2024, 1, 8)


def catalog_rows(*, atomic=False):
    rows = [
        {"code": "000001", "name": "QA证券乙", "exchange": "SZSE", "industry": "QA金融", "list_date": date(2020, 1, 1)},
        {"code": "600001", "name": "QA证券甲", "exchange": "SSE", "industry": "QA制造", "list_date": date(2020, 1, 1)},
    ]
    if atomic:
        rows[1]["name"] = "QA不应提交的改名"
        rows.append({"code": "600002", "name": "QA不应提交的新证券", "exchange": "SSE", "industry": "QA制造", "list_date": date(2020, 1, 1)})
    return rows


def quote_rows(trading_date, *, partial=False, atomic=False, future=False):
    result = []
    for position, instrument in enumerate(catalog_rows(atomic=atomic)):
        if partial and instrument["code"] != "600001":
            continue
        close = 999 if future else (11 if instrument["code"] == "600001" else 21 + position)
        if trading_date == PARTIAL_DATE:
            close += 1
        result.append({"ts_code": instrument["code"] + (".SH" if instrument["exchange"] == "SSE" else ".SZ"),
            "trade_date": trading_date.strftime("%Y%m%d"), "open": close - 1, "high": close + 1, "low": close - 2,
            "close": close, "pre_close": close - 1, "vol": 100 + position, "amount": 123.45})
    return result


class FixtureSource:
    def __init__(self, *, partial=False, atomic=False, fail=False, hold=False):
        self.partial, self.atomic, self.fail = partial, atomic, fail
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def fetch(self, trading_date, cached_daily=None):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise EodSourceError("QA确定性来源失败，无网络调用")
        return catalog_rows(atomic=self.atomic), quote_rows(trading_date, partial=self.partial, atomic=self.atomic), False


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only the dedicated local PostgreSQL 5433 database is permitted")
    namespace = "qa_stock_eod_" + uuid4().hex
    if not re.fullmatch(r"qa_stock_eod_[a-f0-9]{32}", namespace):
        raise RuntimeError("Unsafe QA namespace")
    engine = create_replay_engine(settings)
    engine.sync_engine.hide_parameters = True
    factory = async_sessionmaker(engine, expire_on_commit=False)
    checks = []
    report = {"namespace": namespace, "database_port": 5433, "source_boundary": "deterministic_async_fixture_no_network", "model_calls": 0, "checks": checks, "cleanup": False}
    output = ROOT / "logs" / f"stock-eod-postgres-results-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{namespace[-8:]}.json"

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        if not condition:
            raise AssertionError(name)

    @asynccontextmanager
    async def scoped():
        async with factory() as session:
            await session.execute(text(f'SET LOCAL search_path TO "{namespace}", public'))
            await session.execute(text("SET LOCAL statement_timeout = '15s'"))
            await session.execute(text("SET LOCAL lock_timeout = '10s'"))
            if await session.scalar(text("SELECT current_schema()")) != namespace:
                raise RuntimeError("QA schema isolation check failed")
            yield session

    async def public_fingerprint():
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                result = {}
                for table, ordering in (("stock_instruments", "code"), ("stock_eod_batches", "trade_date")):
                    values = (await connection.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY {ordering})) AS digest FROM public.{table} AS item"))).mappings().one()
                    result[table] = dict(values)
                sequence = (await connection.execute(text("SELECT last_value, is_called FROM public.stock_instruments_id_seq"))).mappings().one()
                result["stock_instruments_sequence"] = dict(sequence)
                return result

    before = None
    tasks = []
    try:
        before = await public_fingerprint()
        async with engine.connect() as connection:
            checksum = await connection.scalar(text("SELECT checksum FROM public.stock_eod_schema_versions WHERE version = '20261018_003_stock_eod'"))
            expected = hashlib.sha256((ROOT / "backend/migrations/20261018_003_stock_eod.sql").read_bytes()).hexdigest()
            check("public_migration_checksum_matches_source", checksum == expected)
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            for table in TABLES:
                await connection.execute(text(f'CREATE TABLE "{namespace}"."{table}" (LIKE public."{table}" INCLUDING ALL)'))
            await connection.execute(text(f'CREATE SEQUENCE "{namespace}"."stock_instruments_id_seq" OWNED BY "{namespace}"."stock_instruments"."id"'))
            await connection.execute(text(f'ALTER TABLE "{namespace}"."stock_instruments" ALTER COLUMN id SET DEFAULT nextval(\'"{namespace}"."stock_instruments_id_seq"\'::regclass)'))
        async with scoped() as session:
            private_sequence = await session.scalar(text("SELECT pg_get_serial_sequence(:table_name, 'id')"), {"table_name": f'"{namespace}"."stock_instruments"'})
            check("private_catalog_sequence_cannot_advance_public_sequence", private_sequence == f"{namespace}.stock_instruments_id_seq")

        source = FixtureSource(hold=True)
        tasks = [asyncio.create_task(run_eod(scoped, source, COMPLETE_DATE)) for _ in range(10)]
        try:
            await asyncio.wait_for(source.started.wait(), timeout=5)

            async def other_claims_finished():
                while sum(task.done() for task in tasks) < 9:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(other_claims_finished(), timeout=5)
            check("ten_concurrent_claims_enter_source_once", source.calls == 1 and sum(task.done() for task in tasks) == 9)
            source.release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
        finally:
            source.release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        check("one_concurrent_completion_and_nine_replays", sum(item["status"] == "completed" for item in results) == 1 and sum(item["status"] == "already_recorded" for item in results) == 9 and source.calls == 1)
        async with scoped() as session:
            stored = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == COMPLETE_DATE))
            complete_version = stored.data_version
            check("completed_snapshot_and_catalog_are_persisted_together", stored.status == "completed" and stored.attempts == 1 and stored.finished_at is not None and stored.snapshot_json["covered_count"] == 2 and await session.scalar(select(func.count()).select_from(StockInstrument)) == 2)
            check("snapshot_records_units_source_hash_and_real_reference", stored.snapshot_json["volume_unit"] == "lot_100_shares" and stored.snapshot_json["price_adjustment"] == "none" and stored.snapshot_json["amount_unit"] == "CNY" and len(complete_version) == 64 and stored.snapshot_json["quotes"][0]["amount"] == 123450)

        partial_source = FixtureSource(partial=True)
        partial = await run_eod(scoped, partial_source, PARTIAL_DATE)
        async with scoped() as session:
            stored = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == PARTIAL_DATE))
            partial_version = stored.data_version
            check("partial_snapshot_retains_truthful_missing_coverage", partial["status"] == "partially_completed" and stored.snapshot_json["covered_count"] == 1 and stored.snapshot_json["expected_count"] == 2 and stored.snapshot_json["missing_codes"] == ["000001"] and partial_source.calls == 1)

        failed_source = FixtureSource(fail=True)
        try:
            await run_eod(scoped, failed_source, FAILED_DATE)
        except EodSourceError:
            pass
        else:
            check("failed_source_remains_an_error", False)
        replay = await run_eod(scoped, failed_source, FAILED_DATE)
        async with scoped() as session:
            previous = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == PARTIAL_DATE))
            failed = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == FAILED_DATE))
            check("failed_date_preserves_previous_success_and_never_retries_source", previous.data_version == partial_version and failed.status == "failed" and failed.snapshot_json == {} and failed_source.calls == 1 and replay["status"] == "already_recorded")

        now = datetime.now(timezone.utc)
        async with scoped() as session:
            session.add(StockEodBatch(trade_date=EXPIRED_DATE, status="running", step="fetching", request_id=uuid4().hex, attempts=1, started_at=now - timedelta(minutes=10), deadline_at=now - timedelta(minutes=5), method_version=METHOD_VERSION, snapshot_json={}))
            await session.commit()
        expired_source = FixtureSource()
        expired = await run_eod(scoped, expired_source, EXPIRED_DATE)
        async with scoped() as session:
            stored = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == EXPIRED_DATE))
            check("expired_running_task_fails_without_another_source_call", expired["status"] == "already_recorded" and stored.status == "failed" and stored.error_code == "eod_execution_expired" and stored.finished_at is not None and expired_source.calls == 0)

        async with engine.begin() as connection:
            await connection.execute(text(f'ALTER TABLE "{namespace}"."stock_eod_batches" ADD CONSTRAINT qa_fail_completed_commit CHECK (trade_date <> DATE \'2024-01-08\' OR status <> \'completed\')'))
        atomic_source = FixtureSource(atomic=True)
        try:
            await run_eod(scoped, atomic_source, ATOMIC_DATE)
        except EodSourceError:
            pass
        else:
            check("forced_atomic_commit_failure_surfaces", False)
        async with scoped() as session:
            names = dict((await session.execute(select(StockInstrument.code, StockInstrument.name))).all())
            failed = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == ATOMIC_DATE))
            check("snapshot_commit_failure_rolls_back_catalog_updates_and_inserts", names == {"000001": "QA证券乙", "600001": "QA证券甲"} and failed.status == "failed" and failed.snapshot_json == {} and failed.data_version is None and atomic_source.calls == 1)

        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        future_date = today + timedelta(days=7)
        future_snapshot = normalize_eod(quote_rows(future_date, future=True), catalog_rows(), future_date, acquired_at=now, has_more=False)
        async with scoped() as session:
            session.add(StockEodBatch(trade_date=future_date, status="completed", step="completed", request_id=uuid4().hex, attempts=1, started_at=now, deadline_at=now + timedelta(seconds=120), finished_at=now, method_version=METHOD_VERSION, data_version=future_snapshot["data_version"], snapshot_json=future_snapshot))
            await session.commit()
        async with scoped() as session:
            latest = await latest_eod(session, today)
            check("latest_eod_excludes_future_failed_and_expired_rows", latest.trade_date == PARTIAL_DATE and latest.data_version == partial_version)
            projected = (await session.execute(eod_bar_statement("600001", today, 10, "postgresql"))).scalars().all()
            check("postgres_jsonpath_projects_one_security_without_full_snapshot", len(projected) == 2 and all(item["code"] == "600001" and "quotes" not in item for item in projected))
            bars = await eod_bars(session, "600001", today, 10)
            check("projected_kline_keeps_order_units_and_excludes_future_prices", [item["time"] for item in bars] == [COMPLETE_DATE.isoformat(), PARTIAL_DATE.isoformat()] and [item["close"] for item in bars] == [11, 12] and all(item["volume"] == 101 for item in bars))
            other = await eod_bars(session, "000001", today, 10)
            check("projection_never_borrows_another_security_for_missing_day", len(other) == 1 and other[0]["time"] == COMPLETE_DATE.isoformat() and other[0]["close"] == 21)
        check("public_catalog_snapshot_and_sequence_remain_unchanged", before == await public_fingerprint())
    except Exception as error:
        report["error_type"] = type(error).__name__
        original = getattr(error, "orig", None)
        cause = getattr(original, "__cause__", None)
        report["database_error"] = {field: getattr(cause, field, None) for field in ("sqlstate", "schema_name", "table_name", "column_name", "constraint_name")}
        print("database_error", json.dumps(report["database_error"]), flush=True)
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL lock_timeout = '10s'"))
                for table in reversed(TABLES):
                    await connection.execute(text(f'DROP TABLE IF EXISTS "{namespace}"."{table}"'))
                await connection.execute(text(f'DROP SEQUENCE IF EXISTS "{namespace}"."stock_instruments_id_seq"'))
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}"'))
            async with engine.connect() as connection:
                report["cleanup"] = await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": namespace}) is None
            check("private_qa_schema_and_test_resources_removed", report["cleanup"])
            if before is not None:
                check("public_data_and_sequence_unchanged_after_cleanup", before == await public_fingerprint())
        finally:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("report", output.name, flush=True)
            await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print("FAILED", type(error).__name__, flush=True)
        sys.exit(1)
