import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import prepare_archive
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


SOURCE_ID = UUID("14831b03-e618-4f9d-a89b-f6db88f3c62b")
SOURCE_VERSION = "2b85dbcca6e90c0899ceaa22208a6f3e9361f843e0c07e1c2f12811bc89204bd"


async def inspect_database(*, archive: bool):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 is permitted")
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '10s'"))
            fingerprint = {}
            fingerprint["history_by_market"] = [dict(item) for item in (await session.execute(text("SELECT market_type, count(*) AS rows, sum(id) AS ids, sum(close::numeric) AS closes, sum(volume::numeric) AS volume FROM public.kline_history GROUP BY market_type ORDER BY market_type"))).mappings()]
            for table, ordering in (("stock_eod_batches", "trade_date"), ("stock_instruments", "code")):
                fingerprint[table] = dict((await session.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY {ordering})) AS digest FROM public.{table} AS item"))).mappings().one())
            fingerprint["sequence"] = dict((await session.execute(text("SELECT last_value, is_called FROM public.stock_instruments_id_seq"))).mappings().one())
            source = await session.scalar(select(StockEodBatch).where(StockEodBatch.id == SOURCE_ID))
            if source is None or source.data_version != SOURCE_VERSION:
                raise ValueError("Known source version required")
            prepared = prepare_archive(source, now=datetime.now(ZoneInfo("Asia/Shanghai")))
            metadata = {name: prepared["batch"][name] for name in ("version", "row_count", "expected_count", "missing_count", "unknown_count")}
            if archive:
                batch = await session.scalar(select(StockDailyArchiveBatch).where(StockDailyArchiveBatch.source_batch_id == SOURCE_ID))
                if batch is None or batch.version != prepared["batch"]["version"]:
                    raise AssertionError("Archive version mismatch")
                columns = [column for column in StockDailyObservation.__table__.columns if column.name != "batch_id"]
                actual = (await session.execute(select(*columns).where(StockDailyObservation.batch_id == batch.id)
                    .order_by(StockDailyObservation.instrument_id).limit(10001))).mappings().all()
                expected = [{name: value for name, value in item.items() if name != "batch_id"} for item in prepared["rows"]]
                if [dict(item) for item in actual] != expected:
                    raise AssertionError("Exact Decimal archive/source readback mismatch")
                metadata["exact_all_rows_readback"] = True
            return fingerprint, metadata
    finally:
        await engine.dispose()


def main():
    output = ROOT / "logs" / f"stock-daily-archive-public-rollout-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    if output.exists():
        raise FileExistsError("Report already exists")
    checks = []
    report = {"checks": checks, "source_calls": 0, "model_calls": 0,
        "proof_scope": "selected_public_legacy_aggregates_and_eod_catalog_digest_not_full_database_certification"}

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    def command(script, *arguments):
        result = subprocess.run([sys.executable, str(ROOT / script), *arguments], cwd=ROOT,
            capture_output=True, text=True, timeout=45)
        if result.returncode != 0:
            raise RuntimeError(f"Local validation subprocess failed: {Path(script).name}")
        return json.loads(result.stdout)

    try:
        before, metadata = asyncio.run(inspect_database(archive=False))
        arguments = ("--batch-id", str(SOURCE_ID), "--expected-data-version", SOURCE_VERSION)
        dry = command("scripts/archive-stock-eod.py", *arguments)
        check("explicit_real_source_dry_run_only", dry["status"] == "dry_run" and dry["row_count"] == metadata["row_count"])
        migration = command("backend/scripts/migrate_stock_daily_archive.py")
        check("additive_migration_checked", migration["checked"] and migration["version"] == "20261019_004_stock_daily_archive")
        applied = command("scripts/archive-stock-eod.py", *arguments, "--apply")
        check("single_real_snapshot_shadow_archive", applied["status"] in ("archived", "already_archived") and applied["row_count"] == 5548
            and applied["expected_count"] == 5558 and applied["unknown_count"] == 19 and applied["missing_count"] == 10)
        repeated = command("scripts/archive-stock-eod.py", *arguments, "--apply")
        check("repeat_is_same_immutable_batch", repeated["status"] == "already_archived" and repeated["id"] == applied["id"]
            and repeated["version"] == applied["version"])
        readiness = command("backend/scripts/migrate_stock_daily_archive.py", "--check")
        check("migration_repeat_check_does_not_apply_ddl", readiness["checked"] and not readiness["applied"])
        after, saved = asyncio.run(inspect_database(archive=True))
        check("all_5548_prices_fractional_volumes_and_amounts_exact", saved["exact_all_rows_readback"])
        check("legacy_stock_futures_history_eod_catalog_sequence_unchanged", before == after)
        accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
        if not accounts[0]["username"].startswith("qa_"):
            raise RuntimeError("Existing QA fixture required")
        with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
            check("market_status_still_requires_auth", client.get("/api/stock/market/sync-status").status_code == 401)
            login = client.post("/api/auth/login", json={**accounts[0], "trading_mode": "virtual"})
            login.raise_for_status()
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            status = client.get("/api/stock/market/sync-status")
            status.raise_for_status()
            check("user_status_read_path_remains_original_eod", status.json()["latest_snapshot"]["data_version"] == SOURCE_VERSION
                and status.json()["latest_snapshot"]["raw_response_evidence"] is False
                and status.json()["latest_snapshot"]["coverage"] == {"expected": 5558, "quoted": 5548, "missing": 10, "unknown": 19})
        report.update(status="verified_shadow_archive_only", migration=migration, archive=applied,
            replay=repeated, source_metadata=saved, user_read_path_changed=False, legacy_public_aggregates_unchanged=True)
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print("report", output.name, flush=True)


if __name__ == "__main__":
    main()
