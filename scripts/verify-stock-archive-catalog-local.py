import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
import httpx
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api.stock_archive_catalog import router
from app.core.deps import get_current_user, get_session
from app.models.stock_daily_archive import StockDailyArchiveBatch
from app.services.stock_daily_archive import FINANCIAL_METADATA, METHOD_VERSION
from app.services.stock_eod import content_hash
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host != "127.0.0.1" or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    public = create_replay_engine(settings)
    namespace = "qa_b31_catalog_" + uuid4().hex
    private = create_async_engine(public.url, echo=False, connect_args={"timeout": 10, "command_timeout": 15, "server_settings": {"search_path": namespace}})
    factory = async_sessionmaker(private, expire_on_commit=False)
    statements = []
    event.listen(private.sync_engine, "before_cursor_execute", lambda conn, cursor, statement, params, ctx, many: statements.append(statement))
    report = {"status": "running", "scope": "real_private_PG_original_ASGI_fixture_auth_metadata_not_prices", "checks": [], "responses": [], "public_reads": [], "public_business_writes": 0, "model_calls": 0, "source_calls": 0}
    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)
    async def digest():
        async with public.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            return {table: await connection.scalar(text(f"SELECT md5(coalesce(string_agg(md5(to_jsonb(item)::text),'' ORDER BY id),'')) FROM public.{table} item")) for table in ("stock_daily_archive_batches", "stock_research_jobs", "ai_trading_tasks")}
    def fixture(index, archived=None):
        day = date(2026, 9, 1) + timedelta(days=index)
        collected = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=9)
        source = hashlib.sha256(f"qa-b31-not-market-data-{index}".encode()).hexdigest()
        return dict(id=uuid4(), source_batch_id=uuid4(), version=content_hash({"method_version": METHOD_VERSION, "source_data_version": source}), source_data_version=source,
            method_version=METHOD_VERSION, trade_date=day, status="completed", source_collected_at=collected, archived_at=archived or collected + timedelta(hours=1),
            row_count=2, expected_count=3, missing_count=1, unknown_count=0, source_metadata={**FINANCIAL_METADATA, "catalog_version": "b" * 64,
                "source_row_count": 2, "verification": "snapshot_only_not_independently_certified", "data_available_at": None,
                "number_encoding": "decimal_of_stored_json_number_not_original_response_text", "source_evidence": {"private": "not-returned"}})
    try:
        async with asyncio.timeout(100):
            before = await digest()
            async with public.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
                await connection.execute(text(f'CREATE TABLE "{namespace}".stock_daily_archive_batches (LIKE public.stock_daily_archive_batches INCLUDING ALL)'))
            rows = [fixture(index) for index in range(25)]
            async with private.begin() as connection:
                await connection.execute(insert(StockDailyArchiveBatch), rows)
            app = FastAPI(); app.include_router(router, prefix="/api/stock/market")
            app.state.stock_archive_ready = True
            identity = SimpleNamespace(id=uuid4(), market_type="stock")
            anonymous = False
            async def auth():
                if anonymous:
                    raise HTTPException(401, "login required")
                return identity
            async def session():
                async with factory() as value, value.begin():
                    await value.execute(text("SET TRANSACTION READ ONLY"))
                    await value.execute(text("SET LOCAL statement_timeout='8s'"))
                    yield value
            app.dependency_overrides[get_current_user] = auth
            app.dependency_overrides[get_session] = session
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                path = "/api/stock/market/archive/versions"
                first = await client.get(path)
                check("first_page_real_pg", first.status_code == 200)
                page = first.json(); report["responses"].append({"query": {"limit": 20, "offset": 0}, "payload": page})
                check("twenty_of_twentyfive", len(page["items"]) == 20 and page["total"] == 25 and page["has_more"])
                check("security_headers", first.headers["Cache-Control"] == "no-store" and first.headers["X-Request-ID"] and first.headers["X-Content-Type-Options"] == "nosniff")
                check("no_internal_evidence", "source_evidence" not in first.text and "not-returned" not in first.text)
                async with private.begin() as connection:
                    await connection.execute(insert(StockDailyArchiveBatch), fixture(25, datetime.now(timezone.utc)))
                query = {"limit": 20, "offset": 20, "snapshot_at": page["snapshot_at"], "membership_token": page["membership_token"]}
                statements.clear()
                second = await client.get(path, params=query)
                sql = [statement for statement in statements if statement.lstrip().upper().startswith(("WITH", "SELECT"))]
                check("single_cte_no_n_plus_one_or_price_reads", len(sql) == 1 and "stock_daily_observations" not in sql[0] and "source_evidence" not in sql[0])
                check("pinned_second_page_five", second.status_code == 200 and len(second.json()["items"]) == 5 and second.json()["total"] == 25 and not second.json()["has_more"])
                report["responses"].append({"query": query, "payload": second.json()})
                check("stable_unique_pages", len({item["batch_id"] for item in page["items"] + second.json()["items"]}) == 25)
                refresh = await client.get(path)
                check("refresh_new_snapshot_includes_append", refresh.status_code == 200 and refresh.json()["total"] == 26 and refresh.json()["snapshot_at"] != page["snapshot_at"])
                filtered_query = {"trade_date": "2026-09-01", "limit": 20, "offset": 0}
                filtered = await client.get(path, params=filtered_query)
                check("date_filter", filtered.status_code == 200 and filtered.json()["total"] == 1 and filtered.json()["items"][0]["trade_date"] == "2026-09-01")
                report["responses"].append({"query": filtered_query, "payload": filtered.json()})
                async with private.connect() as late_connection:
                    transaction = await late_connection.begin()
                    await late_connection.execute(insert(StockDailyArchiveBatch), fixture(26, datetime.fromisoformat(page["snapshot_at"]) - timedelta(seconds=1)))
                    during = await client.get(path, params={"snapshot_at": page["snapshot_at"], "membership_token": page["membership_token"]})
                    check("uncommitted_older_archive_not_in_first_snapshot", during.status_code == 200 and during.json()["total"] == 25)
                    await transaction.commit()
                changed = await client.get(path, params=query)
                check("late_commit_cannot_silently_change_pinned_membership", changed.status_code == 409 and "items" not in changed.json())
                check("snapshot_changed_header", changed.headers.get("X-Stock-Archive-Error") == "snapshot_changed")
                refreshed = (await client.get(path)).json()
                empty = await client.get(path, params={"offset": 10000, "snapshot_at": refreshed["snapshot_at"], "membership_token": refreshed["membership_token"]})
                check("bounded_empty_tail_retains_total", empty.status_code == 200 and empty.json()["items"] == [] and empty.json()["total"] == 27)
                for query in ({"limit": 21}, {"offset": 10001}, {"offset": 1}, {"snapshot_at": page["snapshot_at"]}, {"snapshot_at": "3999-01-01T00:00:00Z", "membership_token": page["membership_token"]}, {"snapshot_at": "2026-10-19T00:00:00Z", "membership_token": page["membership_token"], "trade_date": "2026-10-21"}, {"trade_date": "2026-02-30"}, {"unexpected": "x"}):
                    result = await client.get(path, params=query)
                    check("invalid_query_" + str(len(report["checks"])), result.status_code == 422 and result.headers["X-Request-ID"])
                identity.market_type = "futures"
                check("futures_context_rejected", (await client.get(path)).status_code == 409)
                identity.market_type = "stock"; anonymous = True
                check("anonymous_rejected", (await client.get(path)).status_code == 401)
                anonymous = False; app.state.stock_archive_ready = False
                check("not_ready_not_empty", (await client.get(path)).status_code == 503)
                app.state.stock_archive_ready = True
                for malformed in ({**FINANCIAL_METADATA, "price_adjustment": "qfq"}, {**rows[0]["source_metadata"], "data_available_at": "2026-09-01"}, {name: value for name, value in rows[0]["source_metadata"].items() if name != "data_available_at"}):
                    async with private.begin() as connection:
                        await connection.execute(StockDailyArchiveBatch.__table__.update().where(StockDailyArchiveBatch.id == rows[0]["id"]).values(source_metadata=malformed))
                    result = await client.get(path, params={"trade_date": "2026-09-01"})
                    check("corrupt_metadata_" + str(len(report["checks"])), result.status_code == 503 and "items" not in result.json())
                async with private.begin() as connection:
                    await connection.execute(StockDailyArchiveBatch.__table__.update().where(StockDailyArchiveBatch.id == rows[0]["id"]).values(source_metadata=rows[0]["source_metadata"]))
                check("forward_metadata_restoration", (await client.get(path, params={"trade_date": "2026-09-01"})).status_code == 200)
                async with private.begin() as connection:
                    await connection.execute(text("ALTER TABLE stock_daily_archive_batches RENAME TO qa_hidden_batches"))
                check("physically_missing_archive_table_not_empty_success", (await client.get(path)).status_code == 503)
                async with private.begin() as connection:
                    await connection.execute(text("ALTER TABLE qa_hidden_batches RENAME TO stock_daily_archive_batches"))
                check("restored_archive_table_readable", (await client.get(path)).status_code == 200)
            async with public.begin() as connection:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("SET LOCAL statement_timeout='8s'"))
                from app.schemas.stock_archive_catalog import ArchiveCatalogQuery
                from app.services.stock_archive_catalog import read_archive_catalog
                actual = await read_archive_catalog(connection, ArchiveCatalogQuery(), now=datetime.now(timezone.utc))
                check("existing_public_metadata_readonly", actual["total"] >= 4 and len(actual["items"]) >= 4)
                report["public_reads"].append({"query": {}, "payload": actual})
            check("public_archives_jobs_ai_digests_unchanged", before == await digest())
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
        (ROOT / "logs/stock-b31-private-pg.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "private_responses": len(report["responses"]), "public_reads": len(report["public_reads"]), "error_type": report.get("error_type"), "cleanup_error_type": report.get("cleanup_error_type")}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
