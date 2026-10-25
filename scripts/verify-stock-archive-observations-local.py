import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
from sqlalchemy import event, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api import stock_archive_catalog as api
from app.core.deps import get_current_user, get_session
from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import prepare_archive
from app.services.stock_eod import normalize_eod
from app.services.stock_raw_panel import read_raw_panel
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings
from tests.test_stock_daily_archive import NOW, source_batch
from tests.test_stock_eod import catalog, row


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host != "127.0.0.1" or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    public = create_replay_engine(settings)
    namespace = "qa_b32_observations_" + uuid4().hex
    private = create_async_engine(public.url, echo=False, connect_args={"timeout": 10, "command_timeout": 15, "server_settings": {"search_path": namespace}})
    factory = async_sessionmaker(private, expire_on_commit=False)
    statements = []
    event.listen(private.sync_engine, "before_cursor_execute", lambda connection, cursor, statement, params, context, many: statements.append(statement))
    report = {"status": "running", "scope": "private_real_PG_original_ASGI_fixture_auth_QA_prices_not_public_market_data", "checks": [], "responses": [], "public_reads": [], "source_calls": 0, "model_calls": 0, "public_business_writes": 0}
    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    async def fingerprint():
        async with public.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY")); await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return {table: await connection.scalar(text(f"SELECT md5(coalesce(string_agg(md5(to_jsonb(item)::text),'' ORDER BY id),'')) FROM public.{table} item")) for table in ("stock_daily_archive_batches", "stock_eod_batches", "stock_research_jobs", "ai_trading_tasks")}
    try:
        async with asyncio.timeout(100):
            before = await fingerprint()
            async with public.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
                for table in ("stock_eod_batches", "stock_daily_archive_batches", "stock_daily_observations"):
                    await connection.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
            source = source_batch()
            source.snapshot_json = normalize_eod([row(vol=0.01), row(ts_code="600002.SH", vol=123.456), row(ts_code="920000.BJ")], catalog() + [{"code": "600003", "name": "QA缺口", "exchange": "SSE", "industry": "QA", "list_date": date(2000, 1, 1)}], source.trade_date, acquired_at=NOW, has_more=False, source_evidence={"private_qa_marker": "not_in_response"})
            source.data_version = source.snapshot_json["data_version"]
            prepared = prepare_archive(source, now=NOW + timedelta(seconds=1))
            source_values = {"id": source.id, "trade_date": source.trade_date, "status": source.status, "step": "completed", "request_id": uuid4().hex, "attempts": 1, "started_at": NOW - timedelta(seconds=1), "deadline_at": NOW + timedelta(minutes=1), "finished_at": source.finished_at, "method_version": source.method_version, "data_version": source.data_version, "snapshot_json": source.snapshot_json}
            async with private.begin() as connection:
                await connection.execute(insert(StockEodBatch), source_values)
                await connection.execute(insert(StockDailyArchiveBatch), prepared["batch"])
                await connection.execute(insert(StockDailyObservation), prepared["rows"])
            version = prepared["batch"]["version"]
            app = FastAPI(); app.include_router(api.router, prefix="/api/stock/market"); app.state.stock_archive_ready = True
            user = type("User", (), {"id": uuid4(), "market_type": "stock"})()
            anonymous = False
            async def auth():
                if anonymous:
                    raise HTTPException(401, "login required")
                return user
            async def session():
                async with factory() as value, value.begin():
                    await value.execute(text("SET TRANSACTION READ ONLY")); await value.execute(text("SET LOCAL statement_timeout='6s'"))
                    yield value
            app.dependency_overrides[get_current_user] = auth
            app.dependency_overrides[get_session] = session
            def path(selected=version):
                return f"/api/stock/market/archive/versions/{selected}/observation"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for identifier, expected in (("600001.SH", None), ("600003.SH", "source_missing"), ("920000.BJ", "source_unknown_quarantined"), ("000001.SZ", "outside_snapshot")):
                    statements.clear(); started = perf_counter()
                    response = await client.get(path(), params={"instrument_id": identifier})
                    check("read_" + identifier, response.status_code == 200)
                    payload = response.json()
                    check("three_sql_reads_" + identifier, len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]) == 3)
                    check("safe_headers_" + identifier, response.headers["Cache-Control"] == "no-store" and response.headers["X-Request-ID"] and response.headers["X-Content-Type-Options"] == "nosniff")
                    check("no_private_source_evidence_" + identifier, "source_evidence" not in response.text and "not_in_response" not in response.text)
                    if expected:
                        check("classified_missing_" + identifier, payload["observations"] == [] and payload["missing"][0]["reason"] == expected)
                    else:
                        check("fractional_volume_and_full_batch_count", payload["observations"][0]["volume"] == "0.01" and payload["observations"][0]["amount"] == "456789" and payload["batches"][0]["verified_row_count"] == 2)
                    report["responses"].append({"version": version, "instrument_id": identifier, "payload": payload, "elapsed_ms": round((perf_counter() - started) * 1000, 2)})
                unknown = await client.get(path("f" * 64), params={"instrument_id": "600001.SH"})
                check("unknown_version_404_no_latest_fallback", unknown.status_code == 404 and "observations" not in unknown.json())
                for identifier in ("600001", "600001.SZ", "000001.SH", "600001.SH;DELETE"):
                    check("invalid_id_" + identifier, (await client.get(path(), params={"instrument_id": identifier})).status_code == 422)
                check("extra_query_forbidden", (await client.get(path(), params={"instrument_id": "600001.SH", "versions": "f" * 64})).status_code == 422)
                user.market_type = "futures"
                check("futures_blocked", (await client.get(path(), params={"instrument_id": "600001.SH"})).status_code == 409)
                user.market_type = "stock"; anonymous = True
                check("anonymous_blocked", (await client.get(path(), params={"instrument_id": "600001.SH"})).status_code == 401)
                anonymous = False; app.state.stock_archive_ready = False
                check("not_ready_not_empty_success", (await client.get(path(), params={"instrument_id": "600001.SH"})).status_code == 503)
                app.state.stock_archive_ready = True
                async with private.begin() as connection:
                    await connection.execute(StockDailyObservation.__table__.update().where(StockDailyObservation.code == "600002").values(volume=Decimal("123.457")))
                conflict = await client.get(path(), params={"instrument_id": "600001.SH"})
                check("unselected_corrupt_row_invalidates_selected_quote", conflict.status_code == 409 and "observations" not in conflict.json())
                async with private.begin() as connection:
                    await connection.execute(StockDailyObservation.__table__.update().where(StockDailyObservation.code == "600002").values(volume=Decimal("123.456")))
                    await connection.execute(StockEodBatch.__table__.delete())
                check("missing_source_not_shadow_only_success", (await client.get(path(), params={"instrument_id": "600001.SH"})).status_code == 409)
                async with private.begin() as connection:
                    await connection.execute(insert(StockEodBatch), source_values)
                check("source_and_rows_restored", (await client.get(path(), params={"instrument_id": "600001.SH"})).status_code == 200)
                async with private.connect() as blocker:
                    transaction = await blocker.begin()
                    await blocker.execute(text("LOCK TABLE stock_daily_observations IN ACCESS EXCLUSIVE MODE"))
                    first = asyncio.create_task(client.get(path(), params={"instrument_id": "600001.SH"}))
                    second = asyncio.create_task(client.get(path(), params={"instrument_id": "600002.SH"}))
                    try:
                        for attempt in range(100):
                            if api.observation_gate.locked():
                                break
                            await asyncio.sleep(0.01)
                        check("two_real_reads_admitted", api.observation_gate.locked())
                        check("third_real_read_rejected_without_queue", (await client.get(path(), params={"instrument_id": "600001.SH"})).status_code == 429)
                    finally:
                        await transaction.rollback()
                        completed = await asyncio.gather(first, second)
                    check("admitted_reads_complete_after_lock_release", [item.status_code for item in completed] == [200, 200] and not api.observation_gate.locked())
            async with async_sessionmaker(public, expire_on_commit=False)() as session, session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY")); await session.execute(text("SET LOCAL statement_timeout='8s'"))
                selected = await session.scalar(select(StockDailyArchiveBatch.version).order_by(StockDailyArchiveBatch.trade_date.desc()).limit(1))
                started = perf_counter()
                result = await read_raw_panel(session, [selected], ["000001.SZ"], now=datetime.now(timezone.utc))
                check("public_existing_full_batch_source_equality", len(result["observations"]) == 1 and result["batches"][0]["verified_row_count"] >= 5000)
                report["public_reads"].append({"version": selected, "instrument_id": "000001.SZ", "payload": result, "elapsed_ms": round((perf_counter() - started) * 1000, 2)})
            check("public_archive_eod_research_ai_digests_unchanged", before == await fingerprint())
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await private.dispose()
        try:
            async with public.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
                check("private_schema_removed", await connection.scalar(text("SELECT to_regnamespace(:schema)"), {"schema": namespace}) is None)
        except Exception as error:
            report.update(status="failed", cleanup_error_type=type(error).__name__)
        await public.dispose()
        (ROOT / "logs/stock-b32-private-pg.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "responses": len(report["responses"]), "public_reads": len(report["public_reads"]), "error_type": report.get("error_type"), "cleanup_error_type": report.get("cleanup_error_type")}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
