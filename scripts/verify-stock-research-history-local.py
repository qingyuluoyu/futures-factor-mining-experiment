import asyncio
import json
from datetime import timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api.stock_research_jobs import router
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.models.stock_research_job import StockResearchJob
from app.services.stock_research_jobs import utc_now
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    namespace = "qa_b28_history_" + uuid4().hex
    owner, other = uuid4(), uuid4()
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    report = {"status": "running", "scope": "private_PG_SQL_fixtures_original_ASGI_not_production_JWT_or_financial_results",
              "source_calls": 0, "model_calls": 0, "public_business_writes": 0, "checks": [], "responses": []}

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    async def public_digest():
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            return [await connection.scalar(text(f"SELECT md5(coalesce(string_agg(md5(to_jsonb(item)::text),'' ORDER BY id),'')) FROM public.{table} item"))
                    for table in ("ai_trading_tasks", "stock_research_jobs")]

    try:
        async with asyncio.timeout(80):
            before = await public_digest()
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
                await connection.execute(text(f'CREATE TABLE "{namespace}".stock_research_jobs (LIKE public.stock_research_jobs INCLUDING ALL)'))
            now = utc_now()
            identifiers = {}
            async with factory() as session:
                await session.execute(text(f'SET LOCAL search_path TO "{namespace}", public'))
                check("fixture_target_is_private", str(await session.scalar(text("SELECT 'stock_research_jobs'::regclass::oid"))) != str(await session.scalar(text("SELECT 'public.stock_research_jobs'::regclass::oid"))))
                for user_id, mode, count in ((owner, "virtual", 23), (owner, "live", 1), (other, "virtual", 1)):
                    for kind in ("analysis", "backtest"):
                        for status in ("pending", "running", "completed", "failed", "cancelled", "expired"):
                            for index in range(count):
                                job_id = uuid4()
                                parameters = {"factor_id": "ret_20d"} if kind == "analysis" else {"factor_ids": ["ret_20d"]}
                                parameters.update(start_date="20260713", end_date="20260911")
                                job = StockResearchJob(id=job_id, user_id=user_id, trading_mode=mode, kind=kind,
                                    title=f"ISOLATED QA {kind} {status} {index}", status=status, step=status,
                                    progress=100 if status not in ("pending", "running") else 0, request_id=uuid4().hex,
                                    idempotency_key=uuid4(), request_hash="a" * 64, params_json=parameters,
                                    method_version="qa-contract-fixture", workflow_version="qa-contract-fixture", data_manifest={},
                                    attempts=1 if status in ("running", "completed") else 0,
                                    data_version="fixture-not-financial-data" if status == "completed" else None,
                                    result_json={} if status == "completed" else None, result_bytes=0,
                                    created_at=now - timedelta(seconds=index), updated_at=now,
                                    started_at=now if status in ("running", "completed") else None,
                                    finished_at=now if status not in ("pending", "running") else None,
                                    worker_token=uuid4() if status == "running" else None,
                                    deadline_at=now + timedelta(minutes=3) if status == "running" else None,
                                    lease_expires_at=now + timedelta(seconds=10) if status == "running" else None)
                                session.add(job)
                                if user_id == owner and mode == "virtual":
                                    identifiers[(kind, status)] = job_id
                await session.commit()

            async def sessions():
                async with factory() as session:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    await session.execute(text(f'SET LOCAL search_path TO "{namespace}", public'))
                    yield session

            application = FastAPI()
            application.include_router(router)
            application.dependency_overrides[get_session] = sessions
            application.dependency_overrides[get_auth_context] = lambda: AuthContext(user=context.user, trading_mode=context.mode)
            application.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=True, stock_legacy_sync_enabled=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://local-fixture", timeout=15) as client:
                for status in ("pending", "running", "completed", "failed", "cancelled", "expired"):
                    for offset in (0, 20):
                        query = {"limit": 20, "offset": offset, "kind": "analysis", "status": status}
                        response = await client.get("/api/stock/research/jobs", params=query)
                        check(f"{status}_{offset}_HTTP_headers", response.status_code == 200 and bool(response.headers.get("X-Request-ID")) and response.headers.get("Cache-Control") == "no-store")
                        payload = response.json()
                        check(f"{status}_{offset}_scoped_count", payload["total"] == 23 and len(payload["items"]) == (20 if offset == 0 else 3) and payload["has_more"] == (offset == 0))
                        report["responses"].append({"operation": "list", "query": query, "data": payload})
                identifier = str(identifiers[("analysis", "failed")])
                detail = await client.get(f"/api/stock/research/jobs/{identifier}")
                check("owner_detail_and_parameters", detail.status_code == 200 and detail.json()["id"] == identifier and detail.json()["params"]["factor_id"] == "ret_20d")
                report["responses"].append({"operation": "get", "id": identifier, "data": detail.json()})
                for label, user_id, mode in (("other_user", other, "virtual"), ("other_mode", owner, "live")):
                    context.user.id, context.mode = user_id, mode
                    check(label + "_cannot_read_owner_detail", (await client.get(f"/api/stock/research/jobs/{identifier}")).status_code == 404)
                    page = await client.get("/api/stock/research/jobs", params={"kind": "analysis", "status": "failed"})
                    check(label + "_own_list_isolated", page.status_code == 200 and page.json()["total"] == 1 and page.json()["items"][0]["id"] != identifier)
                context.user.id, context.mode = owner, "virtual"
                check("invalid_status_rejected", (await client.get("/api/stock/research/jobs", params={"status": "invented"})).status_code == 422)
                check("oversized_page_rejected", (await client.get("/api/stock/research/jobs", params={"limit": 51})).status_code == 422)
                context.user.market_type = "futures"
                check("futures_market_rejected", (await client.get("/api/stock/research/jobs")).status_code == 409)
            check("public_tasks_and_research_unchanged", before == await public_digest())
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP TABLE IF EXISTS "{namespace}".stock_research_jobs'))
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}"'))
                check("private_schema_cleaned", await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": namespace}) is None)
        except Exception as error:
            report.update(status="failed", cleanup_error_type=type(error).__name__)
        await engine.dispose()
        output = ROOT / "logs/stock-b28-real-history.json"
        output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf8")
        print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "responses": len(report["responses"]), "error_type": report.get("error_type"), "cleanup_error_type": report.get("cleanup_error_type"), "source_calls": 0, "model_calls": 0, "public_business_writes": 0}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
