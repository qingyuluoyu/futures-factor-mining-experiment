import asyncio
from datetime import datetime, timezone
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

from app.api.stock_research_jobs import router
from app.core.deps import AuthContext, get_auth_context, get_current_user
from app.services.stock_factors.catalog import FACTOR_SPECS
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Dedicated local PG5433 only")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report = {"status": "running", "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
        "scope": "real_market_readonly_ASGI_not_running_deployment", "auth": "fixed_local_stock_context_not_production_JWT"}
    output = ROOT / "logs" / f"stock-completion-b21-live-readonly-{uuid4().hex}.json"
    try:
        async with asyncio.timeout(45):
            async with engine.connect() as connection, connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text("SET LOCAL statement_timeout='5s'"))
                end = await connection.scalar(text("SELECT max(trade_date) FROM public.kline_history WHERE market_type='stock' AND trade_date <= current_date"))
                dates = (await connection.execute(text("SELECT DISTINCT trade_date FROM public.kline_history WHERE market_type='stock' AND trade_date<=:end ORDER BY trade_date DESC LIMIT 61"), {"end": end})).scalars().all()
                if not dates:
                    raise RuntimeError("No saved research dates")
            user = SimpleNamespace(id="b21-readonly-operator", market_type="stock")
            app = FastAPI()
            app.include_router(router)
            app.state.session_factory = factory
            app.dependency_overrides[get_current_user] = lambda: user
            app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=user, trading_mode="virtual")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://readonly", timeout=20) as client:
                response = await client.get("/api/stock/research/inputs", params={"start_date": dates[-1].isoformat(), "end_date": dates[0].isoformat(), "factor_ids": [item.id for item in FACTOR_SPECS], "mktcap_neutral": "true"})
                if response.status_code != 200 or response.headers.get("Cache-Control") != "no-store" or not response.headers.get("X-Request-ID"):
                    raise RuntimeError("Real readonly input endpoint failed")
                report.update(status="passed", result=response.json(), request_id=response.headers["X-Request-ID"], checked_at=datetime.now(timezone.utc).isoformat())
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
