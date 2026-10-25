import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import httpx
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def state():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 permitted")
    engine = create_replay_engine(settings)
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            value = {}
            for name in ("stock_archive_jobs", "stock_daily_archive_batches", "stock_daily_observations", "stock_archive_recoveries"):
                exists = await connection.scalar(text("SELECT to_regclass(:name)"), {"name": "public." + name})
                value[name] = await connection.scalar(text(f"SELECT count(*) FROM public.{name}")) if exists else None
            return value
    finally:
        await engine.dispose()


def main():
    output = ROOT / "logs" / f"stock-archive-recovery-http-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if len(accounts) < 2 or any(not item["username"].startswith("qa_") for item in accounts[:2]):
        raise RuntimeError("Existing QA fixtures required")
    checks = []
    report = {"checks": checks, "source_calls": 0, "model_calls": 0}
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name
    command = [str(ROOT / "backend/.venv/Scripts/python.exe"), str(ROOT / "scripts/recover-stock-archive.py"),
        "--source-job-id", "30a7637b-78e7-4df4-ad8a-7da6764adc34", "--expected-data-version",
        "2b85dbcca6e90c0899ceaa22208a6f3e9361f843e0c07e1c2f12811bc89204bd", "--idempotency-key", str(uuid4()),
        "--reason", "QA权限隔离检查不得实际恢复公库", "--apply"]
    try:
        before = asyncio.run(state())
        environment = os.environ.copy()
        environment.pop("STOCK_ARCHIVE_RECOVERY_TOKEN", None)
        missing = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=35, cwd=ROOT)
        check("real_cli_no_token_rejected_before_database", missing.returncode == 1 and json.loads(missing.stdout)["error_code"] == "access_token_required")
        with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
            check("frontend_login_http200", client.get("/login").status_code == 200)
            health = client.get("http://127.0.0.1:8002/health")
            check("backend_health_endpoint_responds_http200_not_all_function_certification", health.status_code == 200)
            report["health_http_status"] = health.status_code
            check("unauthorized_eod_status_rejected", client.get("/api/stock/market/sync-status").status_code == 401)
            for index, account in enumerate(accounts[:2]):
                login = client.post("/api/auth/login", json={**account, "trading_mode": "virtual"})
                login.raise_for_status()
                token = login.json()["access_token"]
                environment["STOCK_ARCHIVE_RECOVERY_TOKEN"] = token
                rejected = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=35, cwd=ROOT)
                result = json.loads(rejected.stdout)
                check(f"qa_{index}_actual_cli_denies_nonadmin_even_explicit_apply", rejected.returncode == 1 and result["error_code"] == "admin_required" and result["http_status"] == 403)
                check(f"qa_{index}_cli_output_does_not_echo_token_reason_or_traceback", token not in rejected.stdout + rejected.stderr
                    and command[-2] not in rejected.stdout + rejected.stderr and "Traceback" not in rejected.stderr)
                response = client.get("/api/stock/market/sync-status", headers={"Authorization": "Bearer " + token})
                value = response.json()
                check(f"qa_{index}_same_origin_status_compatible_and_traced", response.status_code == 200 and response.headers.get("Cache-Control") == "no-store"
                    and bool(response.headers.get("X-Request-ID")) and value["archive"]["state"] == "completed")
                check(f"qa_{index}_latest_snapshot_still_partial_oct16_not_unclosed_today", value["latest_snapshot"]["trade_date"] == "2026-10-16"
                    and value["latest_snapshot"]["coverage"]["quoted"] == 5548 and value["latest_snapshot"]["raw_response_evidence"] is False
                    and value["today_job"] is None and value["state"] == "before_window")
            report["snapshot_summary"] = {key: value["latest_snapshot"][key] for key in ("trade_date", "coverage", "raw_response_evidence")}
        after = asyncio.run(state())
        check("public_denied_attempts_and_readonly_http_do_not_write_archive_jobs_or_recoveries", before == after and after["stock_archive_recoveries"] == 0)
        report["public_counts"] = after
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
