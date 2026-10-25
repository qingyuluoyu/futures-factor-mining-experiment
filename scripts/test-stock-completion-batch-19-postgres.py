from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import asyncio
import importlib.util
from pathlib import Path
import re

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_market
from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import archive_eod
from app.services.stock_eod import normalize_eod
from app.services.stock_raw_panel import read_raw_panel
from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
from tests.test_stock_daily_archive import source_batch
from tests.test_stock_eod import catalog, row


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    now = datetime.now(timezone.utc)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        database = session.bind

    class PrivateEngine:
        @asynccontextmanager
        async def begin(self):
            async with database.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{namespace}"'))
                yield connection

    tables = ("stock_eod_batches", "stock_daily_archive_batches", "stock_daily_observations", "paper_accounts", "paper_positions", "paper_orders", "paper_ledgers")
    async def fingerprints():
        async with database.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '8s'"))
            return {table: dict((await connection.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) AS digest FROM public.{table} AS item"))).mappings().one()) for table in tables}

    before = await fingerprints()
    await migrate_stock_daily_archive(PrivateEngine())
    async with scoped() as session:
        for table in ("stock_eod_batches", "stock_instruments", "kline_history"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.commit()
    sources = [source_batch(quotes=[row(vol=0.01), row(ts_code="920000.BJ")]), source_batch(quotes=[row(vol=0.02)])]
    sources[1].trade_date = date(2026, 10, 19)
    sources[1].snapshot_json = normalize_eod([row(trade_date="20261019", vol=123.456789)], catalog(), sources[1].trade_date, acquired_at=now, has_more=False)
    sources[1].data_version = sources[1].snapshot_json["data_version"]
    sources[1].finished_at = now
    versions = []
    async with scoped() as session:
        for source in sources:
            await session.execute(insert(StockEodBatch).values(id=source.id, trade_date=source.trade_date, status=source.status,
                step="completed", request_id="b19-private", attempts=1, started_at=source.finished_at, deadline_at=source.finished_at + timedelta(seconds=60),
                finished_at=source.finished_at, method_version=source.method_version, data_version=source.data_version, snapshot_json=source.snapshot_json))
            versions.append((await archive_eod(session, source, now=now))["version"])
        await session.commit()
    instruments = ["600001.SH", "600002.SH", "920000.BJ"]
    async def read(versions_to_read=versions):
        async with scoped() as session:
            await session.commit()
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            result = await read_raw_panel(session, versions_to_read, instruments, now=now)
            check("b19_reader_transaction_really_read_only", await session.scalar(text("SHOW transaction_read_only")) == "on")
            return result
    result = await read()
    check("b19_real_decimal_strings_without_truncation", [item["volume"] for item in result["observations"]] == ["0.01", "123.456789"])
    check("b19_missing_and_unknown_are_not_zero_bars", len(result["missing"]) == 4 and any(item["reason"] == "source_unknown_quarantined" for item in result["missing"]))
    check("b19_full_archive_evidence_not_backtest_or_PIT", result["eligible_for_backtest"] is False and result["data_available_at"] is None and sum(item["verified_row_count"] for item in result["batches"]) == 2)
    check("b19_version_order_deterministic", result["content_sha256"] == (await read(list(reversed(versions))))["content_sha256"])
    try:
        await read([versions[0], "a" * 64])
    except ValueError:
        check("b19_missing_pinned_version_fails_closed", True)
    else:
        check("b19_missing_pinned_version_fails_closed", False)
    specification = importlib.util.spec_from_file_location("stock_raw_export", ROOT / "scripts/export-stock-raw-panel.py")
    cli = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(cli)
    async with database.connect() as connection:
        await connection.execute(text(f'SET search_path TO "{namespace}"'))
        await connection.commit()
        exported = await cli.read_manifest(connection, versions, instruments)
        check("b19_actual_CLI_read_transaction_and_pinned_hash", exported["content_sha256"] == result["content_sha256"])
        await connection.execute(text("RESET search_path"))
        await connection.commit()
    async with scoped() as session:
        await session.commit()
        await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            await session.execute(update(StockDailyObservation).values(volume=Decimal("0")))
        except DBAPIError as error:
            check("b19_database_rejects_writes_in_readonly_transaction", getattr(error.orig, "sqlstate", None) == "25006")
            await session.rollback()
        else:
            check("b19_database_rejects_writes_in_readonly_transaction", False)
    async with scoped() as session:
        saved = await session.scalar(select(StockDailyObservation).where(StockDailyObservation.trade_date == sources[0].trade_date))
        saved.volume = Decimal("999")
        await session.commit()
    try:
        await read()
    except ValueError:
        check("b19_corrupt_stored_row_cannot_export", True)
    else:
        check("b19_corrupt_stored_row_cannot_export", False)
    try:
        async with scoped() as session:
            await read_raw_panel(session, versions, ["000001.SZ"], now=now)
    except ValueError:
        check("b19_unselected_corrupt_row_also_blocks_export", True)
    else:
        check("b19_unselected_corrupt_row_also_blocks_export", False)
    async with scoped() as session:
        await session.execute(update(StockDailyObservation).where(StockDailyObservation.trade_date == sources[0].trade_date).values(volume=Decimal("0.01")))
        await session.execute(update(StockEodBatch).where(StockEodBatch.id == sources[0].id).values(snapshot_json={}))
        await session.commit()
    try:
        await read()
    except ValueError:
        check("b19_corrupt_source_cannot_export_even_valid_rows", True)
    else:
        check("b19_corrupt_source_cannot_export_even_valid_rows", False)
    async with scoped() as session:
        await session.execute(text("DELETE FROM stock_eod_batches WHERE id=:id"), {"id": sources[0].id})
        await session.commit()
    try:
        await read()
    except ValueError:
        check("b19_missing_source_cannot_export", True)
    else:
        check("b19_missing_source_cannot_export", False)
    application.include_router(stock_market.router)
    path = "/api/stock/market/eod-gaps"
    check("b19_unchanged_HTTP_JWT_and_futures_boundary", (await client.get(path)).status_code == 401 and (await client.get(path, headers=headers(owner_id))).status_code == 409)
    application.state.stock_eod_enabled = False
    response = await client.get(path, headers=headers(stock_id))
    check("b19_unchanged_stock_disabled_response_trace_no_store", response.status_code == 200 and response.json()["state"] == "disabled" and response.headers.get("Cache-Control") == "no-store" and bool(response.headers.get("X-Request-ID")))
    check("b19_public_fingerprints_unchanged", before == await fingerprints())


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b19"))
