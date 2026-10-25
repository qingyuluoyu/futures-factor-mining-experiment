from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
from uuid import UUID

import httpx


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="原服务A股页面后端只读验收；可追加本轮浏览器实际新建任务清单")
    parser.add_argument("--browser-jobs", type=Path)
    arguments = parser.parse_args()
    output = ROOT / "logs" / f"stock-pages-http-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if len(accounts) < 2 or any(not value["username"].startswith("qa_") for value in accounts[:2]):
        raise RuntimeError("Existing QA accounts required")
    checks = []
    report = {"checks": checks, "source_calls": 0, "model_calls": 0, "new_research_jobs": 0}
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name
    try:
        with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
            for endpoint in ("/api/stock/market/members", "/api/stock/research/availability", "/api/stock/research/jobs"):
                check("unauthorized_" + endpoint, client.get(endpoint).status_code == 401)
            tokens = []
            for account, mode in ((accounts[0], "virtual"), (accounts[1], "virtual"), (accounts[0], "live")):
                login = client.post("/api/auth/login", json={**account, "trading_mode": mode})
                login.raise_for_status()
                tokens.append({"Authorization": "Bearer " + login.json()["access_token"]})
            owner = tokens[0]
            for path in ("/market/screener", "/backtest", "/factor-lab"):
                check("page_http200_" + path, client.get(path).status_code == 200)
            boards = client.get("/api/stock/market/boards", headers=owner)
            boards.raise_for_status()
            check("stock_industry_catalog_is_real_and_bounded", 0 < len(boards.json()["boards"]) <= 200 and boards.json()["coverage"]["instruments"] == 5558)
            first = client.get("/api/stock/market/members", headers=owner, params={"limit": 50, "offset": 0})
            first.raise_for_status()
            second = client.get("/api/stock/market/members", headers=owner, params={"limit": 50, "offset": 50})
            second.raise_for_status()
            check("screener_real_pagination_has_disjoint_stock_pages", len(first.json()["items"]) == len(second.json()["items"]) == 50
                and not set(item["code"] for item in first.json()["items"]).intersection(item["code"] for item in second.json()["items"]))
            check("screener_preserves_source_date_version_and_no_store_trace", first.json()["as_of"] == "2026-10-16" and len(first.json()["data_version"]) == 64
                and first.headers.get("Cache-Control") == "no-store" and bool(first.headers.get("X-Request-ID")))
            bank = client.get("/api/stock/market/members", headers=owner, params={"industry": "银行", "limit": 50}).json()
            check("industry_filter_returns_actual_42_banks_not_entire_catalog", bank["total"] == 42 and len(bank["items"]) == 42 and all(item["industry"] == "银行" for item in bank["items"]))
            missing = next(item for item in first.json()["items"] if not item["data_available"])
            check("missing_quote_not_fabricated_as_zero", missing["close"] is None and missing["change_pct"] is None and missing["data_status"] == "no_daily")
            quote_only = client.get("/api/stock/market/members", headers=owner, params={"keyword": missing["code"], "data_only": "true"}).json()
            check("quote_only_filter_excludes_real_missing_stock", quote_only["total"] == 0 and quote_only["items"] == [])
            descending = client.get("/api/stock/market/members", headers=owner, params={"industry": "银行", "sort": "change_pct", "direction": "desc"}).json()
            numbers = [item["change_pct"] for item in descending["items"] if item["change_pct"] is not None]
            check("screener_change_sort_uses_real_numbers", numbers == sorted(numbers, reverse=True))
            for params in ({"limit": 101}, {"offset": 10001}, {"sort": "sql"}):
                check("member_schema_rejects_" + next(iter(params)), client.get("/api/stock/market/members", headers=owner, params=params).status_code == 422)
            available = client.get("/api/stock/research/availability", headers=owner)
            check("research_worker_enabled_and_accepting", available.status_code == 200 and available.json()["enabled"] and available.json()["accepting"])
            coverage = client.get("/api/stock/research/coverage", headers=owner, params={"start_date": "2026-07-13", "end_date": "2026-09-11"})
            coverage.raise_for_status()
            check("research_precheck_discloses_real_300_stock_history_not_all_catalog", coverage.json()["available"]["securities"] == 300 and coverage.json()["catalog_count"] == 5558
                and coverage.json()["status"] == "within_limits")
            history = client.get("/api/stock/research/jobs", headers=owner, params={"kind": "backtest", "status": "completed", "limit": 5})
            history.raise_for_status()
            check("completed_backtest_history_exists_for_real_report_path", len(history.json()["items"]) > 0)
            job = history.json()["items"][0]
            result = client.get(f"/api/stock/research/jobs/{job['id']}/result", headers=owner)
            check("persisted_report_returns_stock_metadata_and_data_version", result.status_code == 200 and result.json()["research_job_id"] == job["id"]
                and result.json()["market"] == "stock" and result.json()["research_only"] and bool(result.json()["data_version"]) and len(result.json()["nav"]) > 0)
            for index, headers in enumerate(tokens[1:]):
                check(f"other_identity_or_mode_{index}_cannot_read_private_report", client.get(f"/api/stock/research/jobs/{job['id']}/result", headers=headers).status_code == 404)
            if arguments.browser_jobs is not None:
                created = json.loads(arguments.browser_jobs.read_text(encoding="utf-8"))
                if len(created["jobs"]) != 2 or {value["kind"] for value in created["jobs"]} != {"analysis", "backtest"}:
                    raise ValueError("Expected exactly the two recorded QA browser jobs")
                report["browser_jobs"] = []
                for recorded in created["jobs"]:
                    identifier = str(UUID(recorded["id"]))
                    stored = client.get(f"/api/stock/research/jobs/{identifier}", headers=tokens[1])
                    stored.raise_for_status()
                    check("browser_created_" + recorded["kind"] + "_persists_exact_parameters", stored.json()["status"] == "completed"
                        and stored.json()["kind"] == recorded["kind"] and stored.json()["params"]["start_date"] == "2026-07-13"
                        and stored.json()["params"]["end_date"] == "2026-09-11" and stored.json()["attempts"] == 1)
                    saved = client.get(f"/api/stock/research/jobs/{identifier}/result", headers=tokens[1])
                    check("browser_created_" + recorded["kind"] + "_has_real_result_and_private_owner", saved.status_code == 200
                        and saved.json()["research_job_id"] == identifier and saved.json()["data_version"] == stored.json()["data_version"]
                        and client.get(f"/api/stock/research/jobs/{identifier}/result", headers=owner).status_code == 404)
                    report["browser_jobs"].append({"id": identifier, "kind": recorded["kind"], "status": stored.json()["status"], "data_version": stored.json()["data_version"]})
            report["observed"] = {"trade_date": first.json()["as_of"], "catalog_count": first.json()["coverage"]["instruments"],
                "quoted": first.json()["coverage"]["quoted"], "historical_securities": coverage.json()["available"]["securities"], "existing_report_id": job["id"]}
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
