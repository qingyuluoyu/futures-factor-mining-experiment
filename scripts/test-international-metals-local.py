"""Validate dollar-metal rejection through the running local HTTP/PG stack."""

import json
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "logs/international-metals-http-results.json"


def main():
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if not accounts or not all(account["username"].startswith("qa_") for account in accounts):
        raise RuntimeError("Only existing isolated QA accounts are permitted")
    results = []

    def check(name, condition):
        results.append({"name": name, "passed": bool(condition)})
        OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, "PASS" if condition else "FAIL", flush=True)
        if not condition:
            raise AssertionError(name)

    with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
        check("specifications_require_authentication", client.get("/api/paper/product-specs/XAUUSD.FXCM").status_code == 401)
        response = client.post("/api/auth/login", json={**accounts[0], "trading_mode": "virtual"})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def read(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        def snapshot():
            account = read("/api/paper/account")
            return {
                "financials": {field: account[field] for field in ("balance", "frozen_margin", "realized_pnl", "total_claimed", "last_claim_month")},
                "orders": read("/api/paper/orders?limit=100"),
                "ledger": read("/api/paper/ledgers?limit=100"),
                "tasks": read("/api/ai-trading/tasks"),
                "stock_accounts": read("/api/stock/replay/accounts"),
                "backtest_limits": read("/api/backtest/limits"),
            }

        before = snapshot()
        endpoints = [
            ("/api/paper/estimate", {"price": 4000, "quantity": 1}),
            ("/api/paper/orders", {"direction": "buy", "offset": "open", "order_type": "limit", "price": 4000, "quantity": 1}),
            ("/api/ai-trading/estimate-capital", {"qty": 1}),
            ("/api/ai-trading/tasks", {"timeframe": "1d", "side_mode": "both", "position_mode": "fixed_qty", "auto_start": False}),
            ("/api/backtest/run", {"strategy_type": "n_breakout", "timeframe": "1d", "start_date": "2026-07-01", "end_date": "2026-09-30"}),
        ]
        for symbol in ("XAUUSD", "XAG/USD", "xau_usd.fxcm", "XAGUSD.FXCM", "XAUUSD.OANDA"):
            for path, payload in endpoints:
                response = client.post(path, json={**payload, "symbol": symbol})
                check(f"reject_{symbol}_{path}", response.status_code == 422 and "国际贵金属" in response.text and bool(response.headers.get("X-Request-ID")))
        for symbol in ("XAUUSD.FXCM", "XAGUSD.FXCM"):
            response = client.get("/api/paper/product-specs/" + symbol)
            check(f"spec_path_{symbol}_422_not_500", response.status_code == 422 and "国际贵金属" in response.text)
        check("no_financial_task_stock_or_quota_side_effects", snapshot() == before)
        for symbol, multiplier in (("au2612", 1000), ("ag2612", 15)):
            specs = read("/api/paper/product-specs/" + symbol)
            check(f"domestic_{symbol}_specification_unchanged", specs["default"]["multiplier"] == specs["effective"]["multiplier"] == multiplier)
    print(json.dumps({"checks": len(results), "passed": sum(result["passed"] for result in results)}))


if __name__ == "__main__":
    main()
