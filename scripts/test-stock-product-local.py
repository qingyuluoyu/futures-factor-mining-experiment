import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[1]


def main():
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    assert all(account["username"].startswith("qa_") for account in accounts)
    checks = []
    output = ROOT / "logs/stock-product-http-20261018-1210.json"
    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        output.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name
    base = os.environ.get("QA_BASE_URL", "http://127.0.0.1:3000")
    with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
        check("unauthorized_custom_funds_and_coverage", client.post("/api/paper/account/deposit", json={"amount": "1.00", "idempotency_key": str(uuid4())}).status_code == 401 and client.get("/api/stock/research/coverage").status_code == 401)
        headers = []
        for account, mode in [(accounts[0], "virtual"), (accounts[0], "live"), (accounts[1], "virtual")]:
            response = client.post("/api/auth/login", json={**account, "trading_mode": mode})
            response.raise_for_status()
            headers.append({"Authorization": "Bearer " + response.json()["access_token"]})
        owner = headers[0]
        before = client.get("/api/paper/account", headers=owner).json()
        other_mode = client.get("/api/paper/account", headers=headers[1]).json()
        other_user = client.get("/api/paper/account", headers=headers[2]).json()
        stock_before = client.get("/api/stock/replay/accounts", headers=owner).json()
        body = {"amount": "2000000.25", "idempotency_key": str(uuid4())}
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda _: client.post("/api/paper/account/deposit", headers=owner, json=body), range(8)))
        check("http_custom_funds_not_monthly_limited_and_eight_retries_once", all(response.status_code == 200 for response in responses) and sum(not response.json()["replayed"] for response in responses) == 1)
        after = client.get("/api/paper/account", headers=owner).json()
        check("http_custom_amount_exact_and_account_history_preserved", Decimal(str(after["balance"])) == Decimal(str(before["balance"])) + Decimal(body["amount"]) and all(after[field] == before[field] for field in ("frozen_margin", "realized_pnl", "total_claimed", "last_claim_month")))
        check("http_same_key_different_amount_conflicts", client.post("/api/paper/account/deposit", headers=owner, json={**body, "amount": "1.00"}).status_code == 409)
        check("http_invalid_money_rejected", all(client.post("/api/paper/account/deposit", headers=owner, json={"amount": amount, "idempotency_key": str(uuid4())}).status_code == 422 for amount in ("0", "-1", "1.001", "NaN", "1e6")))
        check("http_funding_user_mode_stock_isolation", client.get("/api/paper/account", headers=headers[1]).json() == other_mode and client.get("/api/paper/account", headers=headers[2]).json() == other_user and client.get("/api/stock/replay/accounts", headers=owner).json() == stock_before)
        coverage_response = client.get("/api/stock/research/coverage", headers=owner)
        coverage_response.raise_for_status()
        coverage = coverage_response.json()
        check("real_research_coverage_default_observed_dates_and_provenance", coverage["coverage_version"] == "stock-research-coverage-v1" and coverage["selected"]["observed_dates"] == 61 and coverage["recommended"] and coverage_response.headers.get("X-Request-ID") and coverage_response.headers.get("Cache-Control") == "no-store")
        check("research_invalid_dates_not_silent", client.get("/api/stock/research/coverage?start_date=not-date", headers=owner).status_code == 422)
        first = date.fromisoformat(coverage["available"]["first_trade_date"])
        absent = client.get("/api/stock/research/coverage", params={"start_date": (first - timedelta(days=15)).isoformat(), "end_date": (first - timedelta(days=10)).isoformat()}, headers=owner).json()
        check("research_absent_interval_no_data_not_defaults", absent["status"] == "no_data" and absent["selected"]["price_rows"] == 0)
        response = client.get("/api/stock/market/boards", headers=owner)
        response.raise_for_status()
        boards = response.json()
        actual = json.loads((ROOT / "logs/stock-eod-actual-20261018-1210.json").read_text(encoding="utf-8"))
        check("actual_full_source_snapshot_and_gaps_visible", boards["data_version"] == actual["data_version"] and boards["coverage"]["quoted"] == actual["covered_count"] == 5548 and boards["source_row_count"] == 5567 and len(boards["missing_codes"]) == 10 and len(boards["unknown_codes"]) == 19)
        check("raw_eod_sector_reference_and_no_fake_missing_prices", boards["price_adjustment"] == "none" and boards["reference_price"] == "vendor_ex_rights_pre_close" and sum(row["quoted_count"] for row in boards["boards"]) == 5548)
        missing = client.get("/api/stock/market/members?keyword=000016", headers=owner).json()["items"][0]
        check("missing_source_quote_not_faked_or_marked_suspended", missing["close"] is None and not missing["data_available"] and missing["data_status"] == "no_daily")
        raw = client.get("/api/stock/kline?code=000006", headers=owner).json()
        adjusted = client.get("/api/stock/kline?code=600519", headers=owner).json()
        check("raw_eod_fallback_never_appended_to_adjusted_history", raw["source"] == "teajoin_eod" and raw["price_adjustment"] == "none" and len(raw["bars"]) == 1 and raw["bars"][0]["time"] == "2026-10-16" and adjusted["price_adjustment"] == "qfq" and adjusted["source"] == "local_import" and len(adjusted["bars"]) == 250)
        check("research_history_not_claimed_full_market_from_eod", coverage["available"]["securities"] < boards["coverage"]["quoted"])
    print(json.dumps({"checks": len(checks), "passed": sum(item["passed"] for item in checks)}))


if __name__ == "__main__":
    main()
