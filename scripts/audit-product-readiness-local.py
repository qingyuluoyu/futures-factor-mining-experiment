import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_factors.coverage import get_research_coverage
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only the dedicated local database is permitted")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    report = {"audit_scope": "readonly_current_metadata_not_source_certification", "source_calls": 0, "model_calls": 0,
        "snapshot_at": datetime.now(timezone.utc).isoformat(), "feature_flags": {key: getattr(settings, key) for key in
            ("stock_replay_enabled", "stock_research_jobs_enabled", "stock_eod_enabled", "stock_eod_sync_enabled", "stock_daily_archive_enabled")}}
    output = ROOT / "logs" / f"full-code-audit-snapshot-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    try:
        async with asyncio.timeout(25), factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '10s'"))
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            report["transaction_read_only"] = await session.scalar(text("SHOW transaction_read_only"))
            report["catalog"] = dict((await session.execute(text("SELECT count(*) AS securities, count(DISTINCT industry) AS industries FROM public.stock_instruments"))).mappings().one())
            report["history"] = dict((await session.execute(text("SELECT count(*) AS rows, count(DISTINCT code) AS securities, min(trade_date)::text AS first_date, max(trade_date)::text AS last_date FROM public.kline_history WHERE market_type='stock'"))).mappings().one())
            report["latest_eod"] = dict((await session.execute(text("SELECT trade_date::text, status, data_version, (snapshot_json->>'expected_count')::int AS expected_count, (snapshot_json->>'covered_count')::int AS covered_count, jsonb_array_length(snapshot_json->'missing_codes') AS missing, jsonb_array_length(snapshot_json->'unknown_codes') AS unknown FROM public.stock_eod_batches WHERE status IN ('completed','partially_completed') ORDER BY trade_date DESC LIMIT 1"))).mappings().one())
            report["replay_datasets"] = dict((await session.execute(text("SELECT count(*) AS versions, count(DISTINCT code) AS securities, min(start_date)::text AS first_date, max(end_date)::text AS last_date FROM public.stock_replay_datasets"))).mappings().one())
            report["archive"] = dict((await session.execute(text("SELECT count(*) AS observations, count(DISTINCT instrument_id) AS securities, count(DISTINCT trade_date) AS dates FROM public.stock_daily_observations"))).mappings().one())
        async with asyncio.timeout(15):
            coverage = await get_research_coverage(factory, date(2021, 10, 16), date(2026, 10, 16), today=date(2026, 10, 19))
        report["five_year_coverage"] = {key: coverage[key] for key in ("requested", "selected", "limits", "status", "warnings")}
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get("http://127.0.0.1:8002/health")
            health = response.json()
            report["health"] = {"http_status": response.status_code, "process_status": health.get("status"),
                "futures_live_ok": health.get("market", {}).get("live", {}).get("ok"),
                "futures_virtual_ok": health.get("market", {}).get("virtual", {}).get("ok")}
        report["status"] = "observed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))
        print("report", output.name)


if __name__ == "__main__":
    asyncio.run(main())
