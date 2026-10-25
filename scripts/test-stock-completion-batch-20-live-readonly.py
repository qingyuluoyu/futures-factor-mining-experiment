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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.stock import router
from app.core.deps import get_current_user, get_session
from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Dedicated local PG5433 only")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    checks = []
    report = {"status": "running", "checks": checks, "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
        "scope": "real_public_market_PG_readonly_local_ASGI_not_running_deployment",
        "auth": "fixed_local_stock_identity_override_not_production_JWT_verification"}
    output = ROOT / "logs" / f"stock-completion-b20-live-readonly-{uuid4().hex}.json"
    def check(name, passed):
        checks.append({"name": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    async def readonly_session():
        async with factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='8s'"))
            await session.execute(text("SET LOCAL lock_timeout='2s'"))
            if await session.scalar(text("SHOW transaction_read_only")) != "on":
                raise RuntimeError("Read only required")
            yield session

    try:
        async with asyncio.timeout(45):
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            async for session in readonly_session():
                source = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date <= today,
                    StockEodBatch.status.in_(("completed", "partially_completed"))).order_by(StockEodBatch.trade_date.desc()).limit(1))
                if source is None:
                    raise RuntimeError("No saved EOD")
                legacy = (await session.scalars(select(KlineHistory.code).where(KlineHistory.market_type == "stock",
                    KlineHistory.trade_date <= today).distinct().order_by(KlineHistory.code).limit(10001))).all()
                catalog = (await session.scalars(select(StockInstrument.code).order_by(StockInstrument.code).limit(10001))).all()
                if len(legacy) > 10000 or len(catalog) > 10000:
                    raise RuntimeError("Inventory exceeds audit bound")
                known, legacy_codes, unknown = set(catalog), set(legacy), set(source.snapshot_json["unknown_codes"])
                raw_quote = next(item for item in source.snapshot_json["quotes"] if item["code"] in known - legacy_codes - unknown)
                adjusted_code = next(code for code in legacy if code in known)
                report["source_data_version"] = source.data_version
                report["source_date"] = source.trade_date.isoformat()
            application = FastAPI()
            application.include_router(router)
            application.state.stock_eod_enabled = True
            application.dependency_overrides[get_session] = readonly_session
            application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="local-readonly-b20", market_type="stock")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://local-readonly", timeout=20) as client:
                response = await client.get(f"/api/stock/kline?code={raw_quote['code']}&limit=3")
                check("raw_HTTP_200_no_store_trace", response.status_code == 200 and response.headers.get("X-Request-ID") and response.headers.get("Cache-Control") == "no-store")
                raw = response.json()
                check("actual_saved_reference_and_volume_reach_HTTP", raw["change_basis"] == "vendor_ex_rights_pre_close" and raw["bars"][-1]["pre_close"] == raw_quote["pre_close"] and raw["bars"][-1]["volume"] == raw_quote["volume"])
                response = await client.get(f"/api/stock/kline?code={adjusted_code}&limit=2")
                check("adjusted_HTTP_200", response.status_code == 200)
                adjusted = response.json()
                check("legacy_series_never_appends_new_raw_bar", adjusted["price_adjustment"] == "qfq" and adjusted["change_basis"] == "adjacent_loaded_adjusted_close" and all("pre_close" not in item for item in adjusted["bars"]))
                report["responses"] = {"raw": raw, "adjusted": adjusted}
            report.update(status="passed", checked_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=True, allow_nan=False, indent=2)
    print(json.dumps({"status": report["status"], "checks": len(checks), "output": output.name, "source_calls": 0, "model_calls": 0, "public_business_writes": 0}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
