import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_research_job import StockResearchJob
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def inspect_job(job_id):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("Only the dedicated local QA database is permitted")
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == UUID(job_id)))
            return {"attempts": job.attempts, "reserved": job.worker_token is not None, "result_bytes": job.result_bytes, "params": job.params_json}
    finally:
        await engine.dispose()


def main():
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if len(accounts) != 2 or not all(account["username"].startswith("qa_") for account in accounts):
        raise RuntimeError("Only isolated QA accounts are permitted")
    checks, job_ids = [], []
    output = ROOT / "logs/stock-research-jobs-http-results-20261018.json"

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        output.write_text(json.dumps({"checks": checks, "job_ids": job_ids}, indent=2), encoding="utf-8")
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    with httpx.Client(base_url=os.environ.get("QA_BASE_URL", "http://127.0.0.1:3000"), timeout=30, trust_env=False) as client:
        prefix = "/api/stock/research"
        check("unauthorized_jobs_rejected", client.get(prefix + "/jobs").status_code == 401)
        tokens = []
        for account, mode in [(accounts[0], "virtual"), (accounts[1], "virtual"), (accounts[0], "live")]:
            response = client.post("/api/auth/login", json={**account, "trading_mode": mode})
            response.raise_for_status()
            tokens.append({"Authorization": "Bearer " + response.json()["access_token"]})
        owner = tokens[0]
        before = client.get("/api/paper/account", headers=owner).json()
        response = client.get(prefix + "/availability", headers=owner)
        available = response.json()
        check("migrated_worker_accepts", response.status_code == 200 and available["enabled"] and available["accepting"])
        check("pagination_bounded", client.get(prefix + "/jobs?limit=51", headers=owner).status_code == 422)

        def create(kind="analysis", **changes):
            parameters = {"start_date": "20260713", "end_date": "20260911"}
            parameters.update({"factor_id": "ret_20d", "horizon": 20} if kind == "analysis" else {"factor_ids": ["ret_20d"], "top_n": 20})
            return {"kind": kind, "parameters": parameters, "idempotency_key": str(uuid4()), **changes}

        def wait(job_id):
            deadline = time.monotonic() + 135
            while time.monotonic() < deadline:
                response = client.get(prefix + "/jobs/" + job_id, headers=owner)
                response.raise_for_status()
                job = response.json()
                if job["status"] not in ("pending", "running"):
                    return job
                time.sleep(0.3)
            raise RuntimeError("QA job did not reach a terminal state within its execution budget")

        body = create()
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: client.post(prefix + "/jobs", headers=owner, json=body), range(2)))
        check("concurrent_idempotent_create", all(response.status_code == 202 for response in responses) and responses[0].json()["id"] == responses[1].json()["id"])
        job_id = responses[0].json()["id"]
        job_ids.append(job_id)
        check("trace_no_store", all(response.headers.get("X-Request-ID") and response.headers.get("Cache-Control") == "no-store" for response in responses))
        check("same_key_different_payload_conflict", client.post(prefix + "/jobs", headers=owner, json={**body, "title": "conflict"}).status_code == 409)
        check("one_active_per_user_mode", client.post(prefix + "/jobs", headers=owner, json=create()).status_code == 429)
        for name, headers in [("other_user", tokens[1]), ("other_mode", tokens[2])]:
            check(name + "_cannot_read_or_cancel", all(client.request(method, prefix + "/jobs/" + job_id + suffix, headers=headers).status_code == 404 for method, suffix in [("GET", ""), ("GET", "/result"), ("POST", "/cancel")]))
        job = wait(job_id)
        check("real_analysis_completes", job["status"] == "completed" and job["attempts"] == 1)
        report = client.get(prefix + "/jobs/" + job_id + "/result", headers=owner).json()
        check("real_snapshot_and_persisted_report", report.get("research_job_id") == job_id and len(report.get("data_version", "")) == 64 and report["data_manifest"]["transaction"]["read_only"] and report["long_short_nav"] and report["research_only"])
        check("completed_report_immutable", client.post(prefix + "/jobs/" + job_id + "/cancel", headers=owner).status_code == 409)
        check("saved_result_size_and_slot_released", (lambda row: row["attempts"] == 1 and not row["reserved"] and row["result_bytes"] > 100)(asyncio.run(inspect_job(job_id))))

        backtest = client.post(prefix + "/jobs", headers=owner, json=create("backtest"))
        backtest.raise_for_status()
        job_ids.append(backtest.json()["id"])
        job = wait(job_ids[-1])
        check("real_backtest_completes", job["status"] == "completed")
        report = client.get(prefix + "/jobs/" + job["id"] + "/result", headers=owner).json()
        check("backtest_nav_costs_and_versions", len(report["nav"]) > 1 and report["rebalance_count"] > 0 and report["metrics"]["periods"] > 0 and report["method_version"] and report["workflow_version"])

        invalid = create(parameters={"factor_id": "ret_20d", "start_date": "20260101", "end_date": "20260930"})
        response = client.post(prefix + "/jobs", headers=owner, json=invalid)
        response.raise_for_status()
        job_ids.append(response.json()["id"])
        job = wait(job_ids[-1])
        check("missing_held_price_fails_without_fabrication", job["status"] == "failed" and job["error"]["status"] == 409 and not job["has_result"])
        check("failed_report_unavailable", client.get(prefix + "/jobs/" + job["id"] + "/result", headers=owner).status_code == 409)
        check("failed_retry_original_key_does_not_reexecute", client.post(prefix + "/jobs", headers=owner, json=invalid).json()["id"] == job["id"] and asyncio.run(inspect_job(job["id"]))["attempts"] == 1)

        response = client.post(prefix + "/jobs", headers=owner, json=create())
        response.raise_for_status()
        job_id = response.json()["id"]
        job_ids.append(job_id)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = client.get(prefix + "/jobs/" + job_id, headers=owner).json()["status"]
            if status == "running":
                break
            time.sleep(0.05)
        check("cancel_running_process", status == "running" and client.post(prefix + "/jobs/" + job_id + "/cancel", headers=owner).json()["status"] == "cancelled")
        time.sleep(1.5)
        check("cancelled_result_rejected_and_compute_slot_acknowledged", client.get(prefix + "/jobs/" + job_id + "/result", headers=owner).status_code == 409 and not asyncio.run(inspect_job(job_id))["reserved"])
        check("cancel_is_idempotent", client.post(prefix + "/jobs/" + job_id + "/cancel", headers=owner).json()["status"] == "cancelled")
        history = client.get(prefix + "/jobs?limit=2&offset=0", headers=owner).json()
        check("history_paginated_persistent", len(history["items"]) == 2 and history["has_more"] and history["total"] >= 4 and all("result_json" not in item for item in history["items"]))
        check("futures_account_unchanged", client.get("/api/paper/account", headers=owner).json() == before)
    print("ALL", len(checks), "CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
