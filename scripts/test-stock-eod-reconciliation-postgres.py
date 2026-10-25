import asyncio
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from sqlalchemy import insert, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION, normalize_eod
from app.services.stock_eod_reconciliation import read_reconciliation
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


specification = importlib.util.spec_from_file_location("reconciliation_cli", ROOT / "scripts/audit-stock-eod-reconciliation.py")
cli = importlib.util.module_from_spec(specification)
specification.loader.exec_module(cli)


async def public_fingerprints(engine):
    result = {}
    async with engine.connect() as connection:
        for name in ("stock_eod_batches", "stock_instruments", "ai_trading_tasks"):
            result[name] = await connection.scalar(text(f"SELECT md5(coalesce(string_agg(row_hash, '' ORDER BY row_hash), '')) FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM public.{name} t) fingerprints"))
    return result


async def main():
    settings = load_runtime_settings()
    cli.validate_local_settings(settings)
    engine = create_replay_engine(settings)
    namespace = "qa_eod_reconcile_" + uuid4().hex
    if not re.fullmatch(r"qa_eod_reconcile_[a-f0-9]{32}", namespace):
        raise RuntimeError("Private schema required")
    private = engine.execution_options(schema_translate_map={None: namespace})
    factory = async_sessionmaker(private, expire_on_commit=False)
    report = {"status": "running", "checks": [], "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
              "scope": "real_private_PG_saved_data_fixtures_not_production_HTTP_JWT_or_source_certification"}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    versions = []
    before = await public_fingerprints(engine)
    try:
        async with asyncio.timeout(40):
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            async with private.begin() as connection:
                for model in (StockInstrument, StockEodBatch):
                    await connection.run_sync(lambda sync, model=model: model.__table__.create(sync))
                instruments = [{"code": code, "name": code, "exchange": "SSE", "industry": "fixture", "list_date": date(2000, 1, 1)} for code in ("600101", "600102")]
                await connection.execute(insert(StockInstrument), instruments)
                for offset in (0, 1):
                    stamp = date(2024, 1, 3) + timedelta(days=offset)
                    acquired = datetime(2024, 1, 3 + offset, 9, tzinfo=timezone.utc)
                    quotes = [{"ts_code": code + ".SH", "trade_date": stamp.strftime("%Y%m%d"), "open": 10, "high": 10,
                               "low": 10, "close": 10, "pre_close": 10, "vol": 123.45, "amount": 100}
                              for code in (["600101", "600190"] if offset == 0 else ["600101", "600102", "600191"])]
                    snapshot = normalize_eod(quotes, instruments, stamp, acquired_at=acquired, has_more=False,
                                             source_evidence={"private_token": "pg-secret-fixture"})
                    versions.append(snapshot["data_version"])
                    await connection.execute(insert(StockEodBatch).values(id=uuid4(), trade_date=stamp, status="partially_completed",
                        step="partially_completed", request_id="private-b25-fixture", attempts=1, started_at=acquired,
                        deadline_at=acquired + timedelta(seconds=60), finished_at=acquired + timedelta(seconds=1),
                        method_version=METHOD_VERSION, data_version=snapshot["data_version"], snapshot_json=snapshot))
            now = datetime(2024, 1, 5, tzinfo=timezone.utc)
            result = await cli.collect_report(factory, *versions, now=now)
            check("cli_actual_readonly_repeatable_read", result["transaction_read_only"] == "on" and result["transaction_isolation"] == "repeatable read")
            rows = {item["code"]: item for item in result["result"]["items"]}
            check("missing_to_matched_quote_not_zero_fill", rows["600102"]["before"]["state"] == "missing" and rows["600102"]["after"]["state"] == "matched_quote")
            check("unknown_disappearance_not_claimed_repaired", rows["600190"]["after"]["state"] == "outside_snapshot")
            check("fixed_versions_reach_result", result["result"]["batches"]["previous"]["data_version"] == versions[0] and result["result"]["batches"]["current"]["data_version"] == versions[1])
            check("source_secret_never_serialized", "pg-secret-fixture" not in json.dumps(result) and "private_token" not in json.dumps(result))
            for name, previous, current in (("missing_version_rejected", versions[0], "f" * 64),
                                             ("duplicate_version_rejected", versions[0], versions[0]),
                                             ("reversed_versions_rejected", versions[1], versions[0])):
                try:
                    await cli.collect_report(factory, previous, current, now=now)
                except ValueError:
                    check(name, True)
                else:
                    check(name, False)
            try:
                async with factory() as session, session.begin():
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    await session.execute(update(StockInstrument).values(name="must-not-write"))
            except DBAPIError as error:
                check("PG_blocks_writes_in_readonly_transaction", error.orig.sqlstate == "25006")
            else:
                check("PG_blocks_writes_in_readonly_transaction", False)
            async with factory() as session, session.begin():
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                pinned = await read_reconciliation(session, *versions, now=now)
                async with private.begin() as writer:
                    await writer.execute(update(StockInstrument).where(StockInstrument.code == "600102").values(name="concurrent-fixture-change"))
                repeated = await read_reconciliation(session, *versions, now=now)
                check("concurrent_catalog_write_does_not_mix_read_transaction", pinned["content_sha256"] == repeated["content_sha256"])
            fresh = await cli.collect_report(factory, *versions, now=now)
            check("new_transaction_reports_catalog_change", fresh["result"]["content_sha256"] != pinned["content_sha256"] and not fresh["result"]["batches"]["current"]["current_catalog_hash_matches"])
            async with private.begin() as connection:
                await connection.execute(update(StockEodBatch).where(StockEodBatch.data_version == versions[1]).values(snapshot_json={}))
            try:
                await cli.collect_report(factory, *versions, now=now)
            except ValueError:
                check("corrupt_fixed_snapshot_fails_not_empty_success", True)
            else:
                check("corrupt_fixed_snapshot_fails_not_empty_success", False)
            check("public_source_catalog_and_AI_tasks_unchanged", await public_fingerprints(engine) == before)
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
            check("private_schema_cleaned", not await connection.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:name)"), {"name": namespace}))
        await engine.dispose()
        output = cli.write_report(report)
        print(json.dumps({**report, "report": str(output)}, ensure_ascii=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
