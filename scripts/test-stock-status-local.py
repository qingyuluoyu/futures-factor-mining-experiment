import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_eod_status import read_sync_status
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def database_status():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 is permitted")
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            assert await session.scalar(text("SHOW transaction_read_only")) == "on"
            result = await read_sync_status(session, now=datetime.now(ZoneInfo("Asia/Shanghai")),
                enabled=True, sync_enabled=True, worker_running=True)
            fingerprint = (await session.execute(text("""
                SELECT
                  (SELECT md5(string_agg(id::text || status || attempts::text || coalesce(data_version, ''), ',' ORDER BY id)) FROM stock_eod_batches) AS eod,
                  (SELECT count(*) FROM kline_history WHERE market_type='stock') AS stock_rows,
                  (SELECT sum(id) FROM kline_history WHERE market_type='stock') AS stock_ids,
                  (SELECT count(*) FROM stock_replay_accounts) AS replay_accounts,
                  (SELECT count(*) FROM stock_replay_orders) AS replay_orders
            """))).mappings().one()
            return result, dict(fingerprint)
    finally:
        await engine.dispose()


def main():
    destination = ROOT / "logs/stock-status-http-results-20261019.json"
    if destination.exists():
        raise FileExistsError("Results already exist")
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if len(accounts) < 2 or any(not account["username"].startswith("qa_") for account in accounts[:2]):
        raise RuntimeError("Existing isolated QA fixtures required")
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    before, fingerprint_before = asyncio.run(database_status())
    with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
        check("unauthenticated_status_rejected", client.get("/api/stock/market/sync-status").status_code == 401)
        for index, account in enumerate(accounts[:2]):
            login = client.post("/api/auth/login", json={**account, "trading_mode": "virtual"})
            login.raise_for_status()
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            response = client.get("/api/stock/market/sync-status")
            check(f"qa_{index}_authenticated_status", response.status_code == 200)
            payload = response.json()
            check(f"qa_{index}_tracing_no_store", bool(response.headers.get("X-Request-ID")) and response.headers.get("Cache-Control") == "no-store")
            check(f"qa_{index}_matches_real_pg_metadata", payload["latest_snapshot"] == before["latest_snapshot"]
                and payload["state"] == before["state"] and payload["today_job"] == before["today_job"])
            check(f"qa_{index}_no_prices_or_private_data", "quotes" not in payload and "items" not in payload
                and "user_id" not in payload and payload["verification"] == "stored_metadata_only_not_independent_certification")
            check(f"qa_{index}_worker_enabled", payload["enabled"] and payload["sync_enabled"] and payload["worker_running"])
    after, fingerprint_after = asyncio.run(database_status())
    check("reads_do_not_change_eod_history_replay_counts", fingerprint_before == fingerprint_after)
    check("snapshot_version_unchanged", before["latest_snapshot"] == after["latest_snapshot"])
    check("old_snapshot_is_not_called_today", before["latest_snapshot"] is not None
        and before["latest_snapshot"]["trade_date"] < before["server_time"][:10] and before["state"] == "before_window"
        and before["today_job"] is None)
    with destination.open("x", encoding="utf-8") as output:
        json.dump({"checks": checks, "observed_status": after, "database_aggregates_unchanged": fingerprint_before == fingerprint_after,
            "proof_scope": "selected_metadata_and_counts_not_cryptographic_full_database_diff", "source_calls": 0, "model_calls": 0},
            output, ensure_ascii=False, allow_nan=False, indent=2)


if __name__ == "__main__":
    main()
