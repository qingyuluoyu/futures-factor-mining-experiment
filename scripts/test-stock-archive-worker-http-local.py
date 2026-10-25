import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import httpx
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def job_state():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 permitted")
    engine = create_replay_engine(settings)
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            jobs = [dict(item) for item in (await connection.execute(text("SELECT id::text, status, attempts, source_batch_id::text, archive_batch_id::text FROM stock_archive_jobs ORDER BY started_at DESC LIMIT 10"))).mappings()]
            history = (await connection.execute(text("SELECT count(*) AS rows, sum(volume::numeric) AS lots FROM stock_daily_observations"))).mappings().one()
            return jobs, dict(history)
    finally:
        await engine.dispose()


def main():
    output = ROOT / "logs" / f"stock-archive-worker-http-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    if output.exists():
        raise FileExistsError("Report already exists")
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if any(not value["username"].startswith("qa_") for value in accounts[:2]) or len(accounts) < 2:
        raise RuntimeError("Existing QA fixtures required")
    checks = []
    report = {"checks": checks, "source_calls": 0, "model_calls": 0}
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name
    try:
        before = asyncio.run(job_state())
        with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
            check("unauthorized_status_rejected", client.get("/api/stock/market/sync-status").status_code == 401)
            for index, account in enumerate(accounts[:2]):
                login = client.post("/api/auth/login", json={**account, "trading_mode": "virtual"})
                login.raise_for_status()
                client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
                response = client.get("/api/stock/market/sync-status")
                check(f"qa_{index}_status_and_tracking", response.status_code == 200 and bool(response.headers.get("X-Request-ID"))
                    and response.headers.get("Cache-Control") == "no-store")
                result = response.json()
                archive = result["archive"]
                check(f"qa_{index}_archive_enabled_ready_and_running", archive["enabled"] and archive["ready"] and archive["worker_running"])
                check(f"qa_{index}_worker_recorded_existing_snapshot_without_duplication", archive["state"] == "completed"
                    and archive["latest_job"]["source_batch_id"] == "14831b03-e618-4f9d-a89b-f6db88f3c62b"
                    and archive["latest_job"]["archive_batch_id"] == "f5ebaa36-febc-4789-a2aa-f4c265cf7ba5" and archive["latest_job"]["attempts"] == 1)
                check(f"qa_{index}_snapshot_and_provenance_unchanged", result["state"] == "before_window" and result["today_job"] is None
                    and result["latest_snapshot"]["trade_date"] == "2026-10-16" and result["latest_snapshot"]["raw_response_evidence"] is False
                    and archive["scope"] == "shadow_only_user_read_path_unchanged" and archive["source_calls"] == archive["model_calls"] == 0)
                check(f"qa_{index}_no_private_error_or_prices_in_archive_status", "error_message" not in archive["latest_job"] and "quotes" not in archive)
                report["observed_status"] = result
        after = asyncio.run(job_state())
        check("reads_dont_duplicate_jobs_or_change_archive_volume", before == after and before[1]["rows"] == 5548)
        check("exactly_one_completed_existing_source_job", sum(item["status"] == "completed"
            and item["source_batch_id"] == "14831b03-e618-4f9d-a89b-f6db88f3c62b" for item in before[0]) == 1)
        report["status"] = "verified"
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print("report", output.name, flush=True)


if __name__ == "__main__":
    main()
