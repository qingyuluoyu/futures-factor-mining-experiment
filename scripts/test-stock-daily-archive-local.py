import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.services.stock_daily_archive import archive_eod, prepare_archive
from app.services.stock_eod import content_hash
from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings
from tests.test_stock_daily_archive import NOW, source_batch
from tests.test_stock_eod import row


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 is permitted")
    namespace = "qa_daily_archive_" + uuid4().hex
    if not re.fullmatch(r"qa_daily_archive_[a-f0-9]{32}", namespace):
        raise RuntimeError("Invalid private namespace")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    checks = []
    output = ROOT / "logs" / f"stock-daily-archive-pg-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{namespace[-8:]}.json"
    report = {"namespace": namespace, "checks": checks, "source_calls": 0, "model_calls": 0, "cleanup": False}

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    @asynccontextmanager
    async def scoped_session(*, repeatable: bool = False):
        async with factory() as session, session.begin():
            if repeatable:
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await session.execute(text(f'SET LOCAL search_path TO "{namespace}"'))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            yield session

    class ScopedEngine:
        @asynccontextmanager
        async def begin(self):
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{namespace}"'))
                yield connection

    async def fingerprint():
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            result = {}
            result["history"] = dict((await connection.execute(text("SELECT count(*) AS rows, sum(id) AS ids, sum(close::numeric) AS closes, sum(volume) AS volume FROM public.kline_history"))).mappings().one())
            for table, order in (("stock_eod_batches", "trade_date"), ("stock_instruments", "code")):
                result[table] = dict((await connection.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY {order})) AS digest FROM public.{table} AS item"))).mappings().one())
            result["sequence"] = dict((await connection.execute(text("SELECT last_value, is_called FROM public.stock_instruments_id_seq"))).mappings().one())
            return result

    before = await fingerprint()
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
        try:
            await migrate_stock_daily_archive(ScopedEngine(), check=True)
        except RuntimeError:
            check("missing_migration_check_refuses_without_ddl", True)
        else:
            check("missing_migration_check_refuses_without_ddl", False)
        first = await migrate_stock_daily_archive(ScopedEngine())
        again = await migrate_stock_daily_archive(ScopedEngine())
        checked = await migrate_stock_daily_archive(ScopedEngine(), check=True)
        check("migration_first_repeat_and_readiness", first["applied"] and not again["applied"] and checked["checked"])
        source = source_batch(quotes=[row(vol=123.45), row(ts_code="920000.BJ", vol=0.01)])
        async def archive_once():
            async with scoped_session() as session:
                return await archive_eod(session, source, now=NOW)
        results = await asyncio.wait_for(asyncio.gather(*(archive_once() for _ in range(6))), timeout=25)
        check("six_concurrent_calls_commit_one_batch", sum(item["status"] == "archived" for item in results) == 1
            and sum(item["status"] == "already_archived" for item in results) == 5 and len({item["id"] for item in results}) == 1)
        repeatable_source = source_batch(quotes=[row(vol=321.123456)])
        async def repeatable_archive():
            async with scoped_session(repeatable=True) as session:
                return await archive_eod(session, repeatable_source, now=NOW)
        repeatable_results = await asyncio.wait_for(asyncio.gather(*(repeatable_archive() for _ in range(4)), return_exceptions=True), timeout=25)
        check("repeatable_read_concurrency_is_success_or_explicit_serialization_failure", all(
            (isinstance(item, dict) and item["status"] in ("archived", "already_archived"))
            or (isinstance(item, DBAPIError) and getattr(item.orig, "sqlstate", None) == "40001") for item in repeatable_results))
        async with scoped_session() as session:
            check("repeatable_read_concurrency_never_duplicates_batch_or_rows", await session.scalar(select(func.count()).select_from(StockDailyArchiveBatch)
                .where(StockDailyArchiveBatch.source_batch_id == repeatable_source.id)) == 1
                and await session.scalar(select(func.count()).select_from(StockDailyObservation).where(StockDailyObservation.batch_id.in_(
                    select(StockDailyArchiveBatch.id).where(StockDailyArchiveBatch.source_batch_id == repeatable_source.id)))) == 1
                and sum(isinstance(item, dict) and item["status"] == "archived" for item in repeatable_results) == 1)
        async with scoped_session() as session:
            batch = await session.scalar(select(StockDailyArchiveBatch).where(StockDailyArchiveBatch.source_batch_id == source.id))
            observed = await session.scalar(select(StockDailyObservation).where(StockDailyObservation.batch_id == batch.id))
            check("fractional_lots_and_amount_exact_numeric_readback", observed.volume == Decimal("123.45") and observed.amount == Decimal("456789"))
            check("unknown_rows_quarantined_missing_scope_retained", batch.row_count == 1 and batch.expected_count == 2
                and batch.missing_count == 1 and batch.unknown_count == 1 and observed.instrument_id == "600001.SH")
            check("legacy_provenance_not_promoted_to_certification", batch.source_metadata["verification"] == "snapshot_only_not_independently_certified"
                and batch.source_metadata["data_available_at"] is None and observed.price_adjustment == "none")
        prepared = prepare_archive(source_batch(quotes=[row(vol=0.01)]), now=NOW)
        prepared["batch"]["version"] = "1" * 64
        async with scoped_session() as session:
            await session.execute(insert(StockDailyArchiveBatch).values(**prepared["batch"]))
        for name, change in [("nan", {"close": Decimal("NaN")}), ("infinity", {"volume": Decimal("Infinity")}),
                ("negative", {"amount": Decimal("-1")}), ("ohlc", {"low": Decimal("99")}),
                ("currency", {"currency": "USD"}), ("date_fk", {"trade_date": source.trade_date.replace(day=15)}),
                ("precision_upper", {"open": Decimal("1e20")})]:
            invalid = {**prepared["rows"][0], **change}
            try:
                async with scoped_session() as session:
                    await session.execute(insert(StockDailyObservation).values(**invalid))
            except DBAPIError as error:
                expected_state = "22003" if name in ("infinity", "precision_upper") else "23503" if name == "date_fk" else "23514"
                check(f"database_rejects_{name}", getattr(error.orig, "sqlstate", None) == expected_state)
            else:
                check(f"database_rejects_{name}", False)
        async with scoped_session() as session:
            await session.execute(insert(StockDailyObservation).values(**prepared["rows"][0]))
            exact = await session.scalar(select(StockDailyObservation.volume).where(StockDailyObservation.batch_id == prepared["batch"]["id"]))
            check("one_share_decimal_lot_is_not_truncated", exact == Decimal("0.01"))
        failed = source_batch(quotes=[row(vol=100.25)])
        try:
            async with scoped_session() as session:
                await archive_eod(session, failed, now=NOW)
                raise RuntimeError("controlled_failure_after_insert")
        except RuntimeError:
            pass
        async with scoped_session() as session:
            check("caller_transaction_rolls_back_batch_and_observations", await session.scalar(select(func.count()).select_from(StockDailyArchiveBatch)
                .where(StockDailyArchiveBatch.source_batch_id == failed.id)) == 0)
        changed = source_batch()
        changed.id = source.id
        changed.snapshot_json["quotes"][0]["volume"] = 999
        changed.snapshot_json["data_version"] = content_hash({key: value for key, value in changed.snapshot_json.items() if key not in ("data_version", "collected_at")})
        changed.data_version = changed.snapshot_json["data_version"]
        try:
            async with scoped_session() as session:
                await archive_eod(session, changed, now=NOW)
        except ValueError:
            check("changed_source_version_does_not_overwrite_archive", True)
        else:
            check("changed_source_version_does_not_overwrite_archive", False)
        async with scoped_session() as session:
            await session.execute(update(StockDailyObservation).where(StockDailyObservation.batch_id == results[0]["id"])
                .values(volume=Decimal("999.99")))
        try:
            async with scoped_session() as session:
                await archive_eod(session, source, now=NOW)
        except ValueError:
            check("idempotent_replay_detects_changed_observation_content", True)
        else:
            check("idempotent_replay_detects_changed_observation_content", False)
        check("public_history_catalog_eod_and_sequence_unchanged", before == await fingerprint())
    finally:
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
