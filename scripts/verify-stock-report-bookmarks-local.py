import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api.stock_research_reports import router
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.models.stock_research_job import StockResearchJob
from app.services.stock_report_bookmarks import set_bookmark
from app.services.stock_research_jobs import ResearchJobService, utc_now
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    base = create_replay_engine(settings)
    namespace = "qa_b30_bookmarks_" + uuid4().hex
    engine = create_async_engine(base.url, echo=False, connect_args={"timeout": 10, "command_timeout": 30, "server_settings": {"search_path": namespace}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    spec = importlib.util.spec_from_file_location("private_bookmark_migration", ROOT / "backend/scripts/migrate_stock_report_bookmarks.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    owner, other = uuid4(), uuid4()
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    report = {"status": "running", "scope": "private_PG_original_ASGI_fixture_auth_not_real_financial_research", "checks": [], "responses": [], "public_business_writes": 0, "model_calls": 0, "source_calls": 0}
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda connection, cursor, statement, parameters, ctx, executemany: statements.append(statement))

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    async def expect_failure(name, callback, error_type=RuntimeError, sqlstate=None):
        try:
            await callback()
        except Exception as error:
            check(name, isinstance(error, error_type) and (sqlstate is None or getattr(getattr(error, "orig", None), "sqlstate", None) == sqlstate))
        else:
            check(name, False)

    async def public_digest():
        async with base.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            return [await connection.scalar(text(f"SELECT md5(coalesce(string_agg(md5(to_jsonb(item)::text),'' ORDER BY id),'')) FROM public.{table} item")) for table in ("ai_trading_tasks", "stock_research_jobs")]

    try:
        async with asyncio.timeout(100):
            before = await public_digest()
            async with base.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
                for table in ("stock_research_jobs", "stock_research_schema_versions"):
                    await connection.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
                await connection.execute(text(f'INSERT INTO "{namespace}".stock_research_schema_versions SELECT * FROM public.stock_research_schema_versions'))
            await expect_failure("pre_migration_check_rejected", lambda: migration.migrate_stock_report_bookmarks(engine, check=True))
            original_path = migration.SCHEMA_PATH
            with tempfile.TemporaryDirectory(prefix="qa_b30_sql_") as directory:
                path = Path(directory) / "failed.sql"
                path.write_text(original_path.read_text(encoding="utf8") + "; SELECT 1/0;", encoding="utf8")
                migration.SCHEMA_PATH = path
                try:
                    await expect_failure("partial_ddl_failure_rejected", lambda: migration.migrate_stock_report_bookmarks(engine, check=False), DBAPIError, "22012")
                finally:
                    migration.SCHEMA_PATH = original_path
            async with engine.connect() as connection:
                check("failed_ddl_and_version_rolled_back", await connection.scalar(text("SELECT to_regclass('stock_report_bookmarks') IS NULL AND to_regclass('stock_report_bookmark_schema_versions') IS NULL")))
            check("first_apply", (await migration.migrate_stock_report_bookmarks(engine, check=False))["applied"])
            check("repeat_apply_idempotent", not (await migration.migrate_stock_report_bookmarks(engine, check=False))["applied"])
            statements.clear()
            check("readonly_schema_check", not (await migration.migrate_stock_report_bookmarks(engine, check=True))["applied"] and any("SET TRANSACTION READ ONLY" in statement for statement in statements))
            async with engine.begin() as connection:
                await connection.execute(text("ALTER TABLE stock_report_bookmarks ALTER COLUMN created_at DROP DEFAULT"))
            await expect_failure("missing_timestamp_default_rejected", lambda: migration.migrate_stock_report_bookmarks(engine, check=True))
            async with engine.begin() as connection:
                await connection.execute(text("ALTER TABLE stock_report_bookmarks ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP"))
            check("forward_repair_check", (await migration.migrate_stock_report_bookmarks(engine, check=True))["checked"])
            async with engine.begin() as connection:
                await connection.execute(text("DROP INDEX ix_stock_report_bookmark_created"))
            await expect_failure("missing_index_rejected", lambda: migration.migrate_stock_report_bookmarks(engine, check=True))
            async with engine.begin() as connection:
                await connection.execute(text("CREATE INDEX ix_stock_report_bookmark_created ON stock_report_bookmarks(created_at,job_id)"))
                await connection.execute(text("UPDATE stock_report_bookmark_schema_versions SET checksum=:checksum"), {"checksum": "f" * 64})
            await expect_failure("checksum_mismatch_rejected", lambda: migration.migrate_stock_report_bookmarks(engine, check=True))
            async with engine.begin() as connection:
                await connection.execute(text("UPDATE stock_report_bookmark_schema_versions SET checksum=:checksum"), {"checksum": hashlib.sha256(original_path.read_bytes()).hexdigest()})
            check("index_and_marker_forward_repaired", (await migration.migrate_stock_report_bookmarks(engine, check=True))["checked"])
            identifiers = []
            async with factory() as session:
                now = utc_now()
                for user_id, mode, count in ((owner, "virtual", 23), (other, "virtual", 1), (owner, "live", 1)):
                    for index in range(count):
                        identifier = uuid4()
                        session.add(StockResearchJob(id=identifier, user_id=user_id, trading_mode=mode, kind="analysis", title=f"ISOLATED QA bookmark {index}", status="completed", step="completed", progress=100,
                            request_id=uuid4().hex, idempotency_key=uuid4(), request_hash="a" * 64, params_json={"factor_id": "ret_20d"}, method_version="qa-bookmark-fixture", workflow_version="qa-bookmark-fixture",
                            data_version="fixture-not-financial-data", data_manifest={}, result_json={"qa": True}, result_bytes=0, attempts=0, created_at=now, updated_at=now, finished_at=now))
                        if user_id == owner and mode == "virtual":
                            identifiers.append(identifier)
                await session.commit()

            async def sessions():
                async with factory() as session:
                    yield session

            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_session] = sessions
            app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=context.user, trading_mode=context.mode)
            app.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture", timeout=15) as client:
                check("capability_ready", (await client.get("/api/stock/research/bookmarks/availability")).json()["enabled"])
                path = f"/api/stock/research/jobs/{identifiers[0]}/bookmark"
                replies = await asyncio.gather(*(client.put(path, json={"bookmarked": True}) for _ in range(5)))
                check("concurrent_true_same_created_time", all(response.status_code == 200 for response in replies) and len({response.json()["created_at"] for response in replies}) == 1)
                report["responses"].append({"operation": "get", "id": str(identifiers[0]), "data": replies[0].json()})
                for identifier in identifiers[1:]:
                    check("put_" + str(identifier), (await client.put(f"/api/stock/research/jobs/{identifier}/bookmark", json={"bookmarked": True})).status_code == 200)
                async with factory() as session:
                    statements.clear()
                    from app.services.stock_report_bookmarks import list_bookmarked_reports
                    page = await list_bookmarked_reports(ResearchJobService(session, owner, "virtual"), limit=20, offset=0, kind="analysis", status="completed")
                    check("single_sql_count_and_page_not_n_plus_one", len(statements) == 3 and len([item for item in statements if "WITH owned_report_bookmarks" in item]) == 1 and page["total"] == 23)
                pages = []
                for offset in (0, 20):
                    query = {"limit": 20, "offset": offset, "kind": "analysis", "status": "completed"}
                    response = await client.get("/api/stock/research/bookmarks", params=query)
                    payload = response.json()
                    check("page_" + str(offset), response.status_code == 200 and payload["total"] == 23 and len(payload["items"]) == (20 if offset == 0 else 3) and payload["has_more"] == (offset == 0) and bool(response.headers.get("X-Request-ID")))
                    pages.extend(item["id"] for item in payload["items"])
                    report["responses"].append({"operation": "list", "query": query, "data": payload})
                check("pages_unique_complete", len(set(pages)) == 23)
                for user_id, mode in ((other, "virtual"), (owner, "live")):
                    context.user.id, context.mode = user_id, mode
                    check("other_scope_read_denied_" + mode, (await client.get(path)).status_code == 404)
                    check("other_scope_write_denied_" + mode, (await client.put(path, json={"bookmarked": False})).status_code == 404)
                    check("other_scope_list_empty_" + mode, (await client.get("/api/stock/research/bookmarks")).json()["total"] == 0)
                context.user.id, context.mode = owner, "virtual"
                check("strict_boolean_HTTP", (await client.put(path, json={"bookmarked": "false"})).status_code == 422)
                replies = await asyncio.gather(*(client.put(path, json={"bookmarked": False}) for _ in range(4)))
                check("concurrent_false_idempotent", all(response.status_code == 200 and not response.json()["bookmarked"] for response in replies))
                async with factory() as session:
                    await set_bookmark(ResearchJobService(session, owner, "virtual"), identifiers[0], True)
                    await session.rollback()
                check("write_transaction_rollback", not (await client.get(path)).json()["bookmarked"])
                context.user.market_type = "futures"
                check("futures_market_denied", (await client.get(path)).status_code == 409)
            check("public_original_tasks_and_reports_unchanged", before == await public_digest())
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await engine.dispose()
        try:
            async with base.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
                check("private_schema_removed", await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": namespace}) is None)
        except Exception as error:
            report.update(status="failed", cleanup_error_type=type(error).__name__)
        await base.dispose()
        output = ROOT / "logs/stock-b30-private-pg.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "responses": len(report["responses"]), "error_type": report.get("error_type"), "cleanup_error_type": report.get("cleanup_error_type"), "public_business_writes": 0}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
