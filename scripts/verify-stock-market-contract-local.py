import asyncio
import json
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
from app.api.stock_market import router
from app.core.deps import AuthContext, get_auth_context, get_current_user
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    engine = create_replay_engine(settings)
    readonly = engine.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
    report = {"status": "running", "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
              "scope": "real_PG_readonly_and_ASGI_fixture_auth_not_production_JWT", "responses": [], "checks": []}

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise AssertionError(name)

    async def task_hash():
        async with readonly.connect() as connection:
            return await connection.scalar(text("SELECT md5(coalesce(string_agg(md5(to_jsonb(item)::text),'' ORDER BY id),'')) FROM ai_trading_tasks item"))

    try:
        async with asyncio.timeout(50):
            before = await task_hash()
            async with readonly.connect() as connection:
                check("database_readonly", await connection.scalar(text("SHOW transaction_read_only")) == "on")
                check("database_repeatable_read", await connection.scalar(text("SHOW transaction_isolation")) == "repeatable read")
                code = await connection.scalar(text("SELECT code FROM stock_instruments WHERE exchange IN ('SSE','SZSE','BSE') ORDER BY code LIMIT 1"))
                check("existing_instrument", code is not None)
            user = SimpleNamespace(id=uuid4(), market_type="stock")
            application = FastAPI()
            application.include_router(router)
            application.state.session_factory = async_sessionmaker(readonly, expire_on_commit=False)
            application.state.stock_eod_enabled = True
            application.dependency_overrides[get_current_user] = lambda: user
            application.dependency_overrides[get_auth_context] = lambda: AuthContext(user=user, trading_mode="virtual")
            routes = [("boards", "/api/stock/market/boards", {"limit": 200}),
                      ("members", "/api/stock/market/members", {"limit": 50}),
                      ("details", "/api/stock/market/details", {"code": code}),
                      ("screen", "/api/stock/market/screen", {"keyword": code})]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://local-fixture", timeout=15) as client:
                for kind, path, parameters in routes:
                    response = await client.get(path, params=parameters)
                    check(kind + "_HTTP200", response.status_code == 200)
                    check(kind + "_request_id", bool(response.headers.get("X-Request-ID")))
                    report["responses"].append({"kind": kind, "query": parameters, "data": response.json()})
                user.market_type = "futures"
                check("futures_context_rejected", (await client.get("/api/stock/market/details", params={"code": code})).status_code == 409)
            check("public_AI_tasks_unchanged", before == await task_hash())
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await engine.dispose()
        output = ROOT / "logs/stock-b27-real-contracts.json"
        output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf8")
        print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "output": output.name, "source_calls": 0, "model_calls": 0, "public_business_writes": 0}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
