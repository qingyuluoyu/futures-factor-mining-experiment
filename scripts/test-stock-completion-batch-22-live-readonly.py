import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

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
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Dedicated local PG5433 only")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report = {"status": "running", "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
        "scope": "real_market_readonly_ASGI_not_running_deployment", "auth": "fixed_local_stock_context_not_production_JWT", "results": []}
    output = ROOT / "logs" / f"stock-completion-b22-live-readonly-{uuid4().hex}.json"
    try:
        async with asyncio.timeout(60):
            async with engine.connect() as connection, connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("SET LOCAL statement_timeout='5s'"))
                code = await connection.scalar(text("SELECT item.code FROM public.stock_instruments item WHERE item.exchange IN ('SSE','SZSE','BSE') AND EXISTS (SELECT 1 FROM public.stock_financial fin WHERE fin.code=item.code) AND EXISTS (SELECT 1 FROM public.stock_valuation val WHERE val.code=item.code) ORDER BY item.code LIMIT 1"))
                if not code:
                    raise RuntimeError("No saved financial and valuation history")
            user = SimpleNamespace(id="b22-readonly-operator", market_type="stock")
            app = FastAPI()
            app.include_router(router)
            app.state.session_factory = factory
            app.dependency_overrides[get_current_user] = lambda: user
            app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=user, trading_mode="virtual")
            as_of = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://readonly", timeout=20) as client:
                for kind in ("financial", "valuation"):
                    pages = []
                    for offset in (0, 2):
                        response = await client.get("/api/stock/market/fundamentals", params={"code": code, "kind": kind, "as_of": as_of, "limit": 2, "offset": offset})
                        if response.status_code != 200 or response.headers.get("Cache-Control") != "no-store" or not response.headers.get("X-Request-ID"):
                            raise RuntimeError("Real readonly history endpoint failed")
                        result = response.json()
                        if result["transaction"] != "postgresql_repeatable_read_readonly" or result["point_in_time_certified"] is not False:
                            raise RuntimeError("Readonly or provenance boundary failed")
                        report["results"].append({"request_id": response.headers["X-Request-ID"], "result": result})
                        pages.append(result)
                    key = "report_date" if kind == "financial" else "trade_date"
                    dates = [row[key] for page in pages for row in page["rows"]]
                    if dates != sorted(set(dates), reverse=True) or pages[0]["counts"] != pages[1]["counts"]:
                        raise RuntimeError("Recorded pages inconsistent; not an immutable snapshot claim")
            report.update(status="passed", checked_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=True, allow_nan=False, indent=2)
    print(json.dumps({"status": report["status"], "output": output.name, "source_calls": 0, "model_calls": 0, "public_business_writes": 0}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
