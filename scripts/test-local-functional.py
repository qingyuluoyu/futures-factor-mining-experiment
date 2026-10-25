from __future__ import annotations

import json
import math
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "logs" / "qa-functional-2026-10-17.json"
ACCOUNTS_PATH = PROJECT_ROOT / "logs" / "qa-local-accounts.json"
RESULTS: list[dict[str, Any]] = []


def record(name: str, passed: bool, details: Any, status: str | None = None) -> None:
    result = {"name": name, "status": status or ("pass" if passed else "fail"), "details": details}
    RESULTS.append(result)
    REPORT_PATH.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


def call(client: httpx.Client, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    try:
        response = client.request(method, path, json=payload)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {"non_json": True, "bytes": len(response.content)}
    except httpx.HTTPError as error:
        return 0, {"transport_error": type(error).__name__}


def rows(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "quotes", "contracts", "positions", "orders"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def login(account: dict[str, str], mode: str = "live") -> httpx.Client:
    client = httpx.Client(base_url="http://127.0.0.1:3000", timeout=90, trust_env=False)
    status, data = call(client, "POST", "/api/auth/login", {**account, "trading_mode": mode})
    if status != 200:
        raise RuntimeError(f"Test account login failed: {status}")
    client.headers["Authorization"] = "Bearer " + data["access_token"]
    return client


def prepare_accounts() -> list[dict[str, str]]:
    if ACCOUNTS_PATH.exists():
        return json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    accounts = []
    with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
        for label in ("owner", "other"):
            account = {"username": f"qa_{label}_{uuid4().hex[:8]}", "password": secrets.token_urlsafe(24)}
            status, _ = call(client, "POST", "/api/auth/register", account)
            if status not in (200, 201):
                raise RuntimeError(f"Test account registration failed: {status}")
            accounts.append(account)
    ACCOUNTS_PATH.write_text(json.dumps(accounts), encoding="utf-8")
    return accounts


def main() -> None:
    accounts = prepare_accounts()
    owner, other = login(accounts[0]), login(accounts[1])
    virtual = login(accounts[0], "virtual")
    anonymous = httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False)
    record("test_accounts", True, {"users": [account["username"] for account in accounts], "scope": "new local QA users only"})

    protected = ["/api/stock/factors", "/api/watchlist", "/api/paper/account", "/api/ai/models", "/api/admin/users"]
    for path in protected:
        status, _ = call(anonymous, "GET", path)
        record("anonymous_denied " + path, status in (401, 403), {"http": status})
    status, _ = call(owner, "GET", "/api/admin/users")
    record("nonadmin_denied", status == 403, {"http": status})

    paths = [
        "/api/auth/me", "/api/users/me/market", "/api/stock/data-status", "/api/stock/universe?limit=10",
        "/api/stock/industries", "/api/stock/factors", "/api/stock/kline?code=600519&limit=50",
        "/api/stock/valuation?code=600519&start=2025-12-01&end=2025-12-31", "/api/stock/financials?code=600519",
        "/api/market/contracts/by-code", "/api/market/channel-status", "/api/paper/account",
        "/api/paper/orders", "/api/paper/positions", "/api/paper/ledgers", "/api/paper/product-specs",
        "/api/backtest/limits", "/api/factor-lab/meta", "/api/factor-lab/history?symbol=rb2701",
        "/api/factor-lab/favorites", "/api/factor-mining/tasks", "/api/factor-mining/supported",
        "/api/strategy-folders?kind=factor", "/api/ai-trading/tasks", "/api/ai-anchor/status",
        "/api/ai/providers", "/api/ai/models", "/api/ai/conversations", "/api/ai/tools",
        "/api/ai/knowledge/documents", "/api/notifications", "/api/notifications/unread-count",
        "/api/users/me/notify-settings", "/api/indicators/settings", "/api/price-alerts",
        "/api/news/latest", "/api/ai-anchor/indicators", "/api/ai-anchor/voices",
    ]
    for path in paths:
        status, data = call(owner, "GET", path)
        record("read " + path, status == 200, {"http": status, "count": len(rows(data)), "error": str(data.get("detail", ""))[:140] if isinstance(data, dict) else ""})

    status, tree = call(owner, "GET", "/api/market/contracts/by-code")
    product = next((value for code, value in tree.items() if code.lower() == "rb"), {}) if status == 200 else {}
    symbol = product.get("master") or "rb2701"
    for mode, client in (("live", owner), ("virtual", virtual)):
        status, quotes = call(client, "GET", "/api/market/quotes")
        record("futures_quotes " + mode, status == 200 and bool(rows(quotes)), {"http": status, "count": len(rows(quotes))})
    status, kline = call(owner, "GET", f"/api/market/kline?symbol={symbol}&period=1d&limit=100")
    record("futures_kline", status == 200 and bool(kline.get("data") or kline.get("bars")), {"http": status, "keys": list(kline) if isinstance(kline, dict) else [], "error": kline.get("detail") if isinstance(kline, dict) else None})

    for market in ("futures", "stock", "futures"):
        status, data = call(owner, "PUT", "/api/users/me/market", {"market_type": market})
        record("market_switch " + market, status == 200 and data.get("market_type") == market, {"http": status})
    status, _ = call(owner, "PUT", "/api/users/me/market", {"market_type": "bad-market"})
    record("invalid_market_rejected", status in (400, 422), {"http": status})

    created = []
    for market, selected in (("futures", symbol), ("stock", "600519")):
        status, item = call(owner, "POST", "/api/watchlist", {"contract_symbol": selected, "contract_name": "QA " + market, "market_type": market})
        created.append((market, item.get("id")))
        record("watchlist_create " + market, status == 201, {"http": status})
        status, duplicate = call(owner, "POST", "/api/watchlist", {"contract_symbol": selected, "contract_name": "QA duplicate", "market_type": market})
        record("watchlist_duplicate " + market, status == 409, {"http": status})
        status, listing = call(owner, "GET", "/api/watchlist?market=" + market)
        record("watchlist_market_read " + market, status == 200 and len(listing) == 1 and listing[0].get("contract_symbol") == selected, {"http": status, "symbols": [entry.get("contract_symbol") for entry in listing] if isinstance(listing, list) else []})
        if item.get("id"):
            status, _ = call(other, "DELETE", "/api/watchlist/" + item["id"])
            record("watchlist_cross_user_denied " + market, status == 404, {"http": status})
    status, invalid_item = call(owner, "POST", "/api/watchlist", {"contract_symbol": "600036", "contract_name": "QA invalid", "market_type": "invalid"})
    record("invalid_watchlist_market_rejected", status in (400, 422), {"http": status})
    if status == 201 and invalid_item.get("id"):
        created.append(("invalid", invalid_item["id"]))

    status, claim = call(virtual, "POST", "/api/paper/account/claim", {})
    record("virtual_funds_claim", status == 200, {"http": status, "keys": list(claim) if isinstance(claim, dict) else []})
    status, quotes = call(virtual, "GET", "/api/market/quotes")
    reference = next((quote for quote in rows(quotes) if quote.get("symbol", "").lower() == symbol.lower()), None)
    if not reference:
        reference = next((quote for quote in rows(quotes) if float(quote.get("last_price") or 0) > 0), None)
    if reference:
        trade_symbol, price = reference["symbol"], reference["last_price"]
        order = {"symbol": trade_symbol, "direction": "buy", "offset": "open", "order_type": "market", "price": price, "quantity": 1}
        status, opened = call(virtual, "POST", "/api/paper/orders", order)
        record("virtual_market_open", status == 200 and opened.get("status") == "filled", {"http": status, "order_status": opened.get("status"), "detail": opened.get("detail")})
        if status == 200:
            close_order = dict(order, direction="sell", offset="close")
            status, closed = call(virtual, "POST", "/api/paper/orders", close_order)
            record("virtual_market_close", status == 200 and closed.get("status") == "filled", {"http": status, "order_status": closed.get("status"), "detail": closed.get("detail")})
        limit_order = dict(order, order_type="limit", price=round(float(price) * 0.92, 2))
        status, pending = call(virtual, "POST", "/api/paper/orders", limit_order)
        record("virtual_limit_order", status == 200 and pending.get("status") == "pending", {"http": status, "order_status": pending.get("status")})
        if status == 200 and pending.get("id"):
            status, _ = call(virtual, "DELETE", "/api/paper/orders/" + pending["id"])
            record("virtual_cancel_order", status == 200, {"http": status})
    else:
        record("virtual_order_chain", False, "No valid reference quote; no invented prices or quotes used", "blocked")

    for quantity in (0, -1):
        status, _ = call(virtual, "POST", "/api/paper/orders", {"symbol": symbol, "direction": "buy", "offset": "open", "order_type": "market", "price": 3000, "quantity": quantity})
        record("invalid_order_quantity " + str(quantity), status == 422, {"http": status})

    status, factors = call(owner, "GET", "/api/stock/factors")
    for factor in rows(factors):
        started = time.monotonic()
        status, result = call(owner, "POST", "/api/stock/factors/analysis", {"factor_id": factor["id"], "start_date": "20250101", "end_date": "20251231", "horizon": 20, "groups": 5})
        record("stock_analysis " + factor["id"], status == 200 and not result.get("error") and bool(result.get("decay")), {"http": status, "seconds": round(time.monotonic() - started, 2), "error": result.get("error") or result.get("detail"), "ic": result.get("ic")})
    base = {"factor_ids": ["ret_20d"], "start_date": "20250101", "end_date": "20251231", "top_n": 20, "commission_rate": 0.0003, "slippage_rate": 0.001}
    first_result = None
    for frequency in ("month", "week"):
        for weight in ("equal", "mktcap", "factor"):
            status, result = call(owner, "POST", "/api/stock/backtest", dict(base, rebalance=frequency, weight=weight))
            valid_nav = bool(result.get("nav")) and all(isinstance(point.get("nav"), (int, float)) and math.isfinite(point["nav"]) for point in result.get("nav", []))
            record("stock_backtest " + frequency + " " + weight, status == 200 and not result.get("error") and valid_nav, {"http": status, "nav_points": len(result.get("nav", [])), "trades": len(result.get("trades", [])), "error": result.get("error") or result.get("detail")})
            if frequency == "month" and weight == "equal":
                first_result = result
    status, repeated = call(owner, "POST", "/api/stock/backtest", dict(base, rebalance="month", weight="equal"))
    record("stock_repeatability_current_database", status == 200 and bool((first_result or {}).get("nav")) and repeated == first_result, {"http": status, "scope": "same current DB, no immutable data-version guarantee"})

    status, result = call(owner, "POST", "/api/stock/factors/analysis", {"factor_id": "invalid_factor", "start_date": "20250101", "end_date": "20251231"})
    record("unknown_stock_factor_rejected", status in (400, 422), {"http": status, "detail": result.get("detail")})
    status, result = call(owner, "POST", "/api/stock/backtest", dict(base, rebalance="invalid", weight="invalid"))
    record("invalid_stock_enums_rejected", status == 422, {"http": status, "error": result.get("error") or result.get("detail")})
    status, universe = call(owner, "GET", "/api/factor-mining/symbols")
    stock_codes = [item.get("code") for item in rows(universe) if str(item.get("code", "")).isdigit() and len(str(item.get("code"))) == 6]
    record("futures_mining_excludes_stock_symbols", status == 200 and not stock_codes, {"http": status, "stock_symbols_leaked": len(stock_codes), "sample": stock_codes[:3]})

    for market, item_id in created:
        if item_id:
            status, _ = call(owner, "DELETE", "/api/watchlist/" + item_id)
            record("cleanup_QA_watchlist " + market, status == 204, {"http": status})
    for client in (owner, other, virtual, anonymous):
        client.close()
    record("test_finished", True, {"at": datetime.now(timezone.utc).isoformat(), "report": str(REPORT_PATH)})


def remaining() -> None:
    RESULTS.extend(json.loads(REPORT_PATH.read_text(encoding="utf-8")))
    accounts = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    owner, other, virtual = login(accounts[0]), login(accounts[1]), login(accounts[0], "virtual")
    base = {"factor_ids": ["ret_20d"], "start_date": "20250101", "end_date": "20251231", "top_n": 20, "commission_rate": 0.0003, "slippage_rate": 0.001}
    for weight in ("equal", "mktcap", "factor"):
        status, result = call(owner, "POST", "/api/stock/backtest", dict(base, rebalance="week", weight=weight))
        invalid = sum(not isinstance(point.get("nav"), (int, float)) or not math.isfinite(point["nav"]) for point in result.get("nav", []))
        record("stock_backtest week " + weight, status == 200 and bool(result.get("nav")) and invalid == 0, {"http": status, "invalid_nav_points": invalid, "nav_points": len(result.get("nav", [])), "error": result.get("error") or result.get("detail")})
    status, first = call(owner, "POST", "/api/stock/backtest", dict(base, rebalance="month", weight="equal"))
    status, second = call(owner, "POST", "/api/stock/backtest", dict(base, rebalance="month", weight="equal"))
    record("stock_repeatability_current_database", status == 200 and bool(first.get("nav")) and first == second, {"http": status})
    for name, path, payload in (
        ("unknown_stock_factor_rejected", "/api/stock/factors/analysis", {"factor_id": "invalid_factor", "start_date": "20250101", "end_date": "20251231"}),
        ("invalid_stock_enums_rejected", "/api/stock/backtest", dict(base, rebalance="invalid", weight="invalid")),
    ):
        status, result = call(owner, "POST", path, payload)
        record(name, status in (400, 422), {"http": status, "detail": result.get("detail"), "non_json": result.get("non_json")})
    started = time.monotonic()
    status, result = call(owner, "POST", "/api/stock/backtest", dict(base, start_date="20210101", rebalance="month", weight="equal"))
    record("stock_5year_response_time", status == 200 and time.monotonic() - started < 300, {"http": status, "seconds": round(time.monotonic() - started, 2), "scope": "response and performance only, financial correctness tested separately"})
    status, universe = call(owner, "GET", "/api/factor-mining/symbols")
    leaked = [item.get("code") for item in rows(universe) if str(item.get("code", "")).isdigit() and len(str(item.get("code"))) == 6]
    record("futures_mining_excludes_stock_symbols", status == 200 and not leaked, {"http": status, "stock_symbols_leaked": len(leaked), "sample": leaked[:3]})
    status, tree = call(owner, "GET", "/api/market/contracts/by-code")
    symbol = next((value.get("master") for code, value in tree.items() if code.lower() == "rb"), "rb2701")
    status, backtest = call(owner, "POST", "/api/backtest/run", {"strategy_type": "ma_cross", "symbol": symbol, "timeframe": "1d", "start_date": "2025-01-01", "end_date": "2025-06-30", "initial_cash": 1000000})
    record("futures_real_history_backtest", status == 200 and not backtest.get("error"), {"http": status, "keys": list(backtest), "detail": backtest.get("detail")})
    status, search = call(owner, "POST", "/api/factor-lab/search", {"symbol": symbol, "timeframe": "1d", "population": 10, "generations": 3, "top_n": 3, "seed": 42})
    record("futures_factor_search_and_persistence", status == 200 and search.get("bars", 0) > 0, {"http": status, "bars": search.get("bars"), "champions": len(search.get("champions", [])), "detail": search.get("detail")})
    if status == 200:
        status, history = call(owner, "GET", "/api/factor-lab/history?symbol=" + symbol)
        record("futures_factor_saved_history", status == 200 and bool(rows(history)), {"http": status, "entries": len(rows(history))})
    status, task = call(virtual, "POST", "/api/ai-trading/tasks", {"name": "QA lifecycle", "strategy_type": "ma_cross", "symbol": symbol, "timeframe": "1d"})
    record("quant_task_create", status == 201, {"http": status, "status": task.get("status"), "detail": task.get("detail")})
    if status == 201 and task.get("id"):
        status, _ = call(other, "GET", "/api/ai-trading/tasks/" + task["id"])
        record("quant_task_cross_user_denied", status == 404, {"http": status})
        status, started = call(virtual, "POST", "/api/ai-trading/tasks/" + task["id"] + "/start", {})
        record("virtual_quant_start_matches_7x24", status == 200 and started.get("status") == "running", {"http": status, "status": started.get("status"), "pause_reason": started.get("pause_reason")})
        status, _ = call(virtual, "POST", "/api/ai-trading/tasks/" + task["id"] + "/pause", {})
        expected = 200 if started.get("status") == "running" else 400
        record("quant_task_pause_state_guard", status == expected, {"http": status, "previous_status": started.get("status")})
        status, _ = call(virtual, "POST", "/api/ai-trading/tasks/" + task["id"] + "/stop?force_close=false", {})
        record("quant_task_stop", status == 200, {"http": status})
        status, _ = call(virtual, "DELETE", "/api/ai-trading/tasks/" + task["id"])
        record("cleanup_QA_quant_task", status in (200, 204), {"http": status})
    status, watchlist = call(owner, "GET", "/api/watchlist?market=futures")
    if status == 200 and watchlist:
        item = watchlist[0]
        call(virtual, "PUT", "/api/watchlist/sort", {"items": [{"id": item["id"], "sort_order": 123}]})
        status, listing = call(owner, "GET", "/api/watchlist?market=futures")
        actual = next(entry["sort_order"] for entry in listing if entry["id"] == item["id"])
        record("watchlist_mode_sort_isolation", actual == item["sort_order"], {"after_virtual_request_live_sort": actual, "original": item["sort_order"]})
    for market in ("futures", "stock"):
        status, listing = call(owner, "GET", "/api/watchlist?market=" + market)
        for item in rows(listing):
            if item.get("contract_name", "").startswith("QA "):
                call(owner, "DELETE", "/api/watchlist/" + item["id"])
    record("remaining_checks_finished", True, {"at": datetime.now(timezone.utc).isoformat(), "initial_harness_issue": "handled null NAV as failure and resumed remaining checks"})
    for client in (owner, other, virtual):
        client.close()


if __name__ == "__main__":
    try:
        if "--remaining" in sys.argv:
            remaining()
        else:
            main()
    except Exception as error:
        record("harness_failure", False, {"type": type(error).__name__, "message": str(error)[:180]})
        sys.exit(1)
    sys.exit(1 if any(result["status"] == "fail" for result in RESULTS) else 0)
