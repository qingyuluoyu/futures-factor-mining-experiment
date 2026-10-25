"""本机真实 HTTP/PG 回归；仅操作 logs 中已有 qa_ 测试用户的新回放账户。"""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_replay import StockReplayDataset
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


def money(value):
    return value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def fees(notional, sell=False):
    return money(max(Decimal("5"), notional * Decimal(".0003"))) + money(notional * Decimal(".00001")) + (money(notional * Decimal(".0005")) if sell else Decimal("0"))


async def raw_snapshot(dataset_id):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("This regression only permits the dedicated local QA database")
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            row = await session.scalar(select(StockReplayDataset).where(StockReplayDataset.id == __import__("uuid").UUID(dataset_id)))
            return row.bars_json
    finally:
        await engine.dispose()


def main():
    results = []
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    if len(accounts) != 2 or not all(account["username"].startswith("qa_") for account in accounts):
        raise RuntimeError("Only isolated QA accounts are permitted")

    def check(name, condition):
        results.append({"name": name, "passed": bool(condition)})
        (ROOT / "logs/stock-replay-http-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    def login(account, mode):
        client = httpx.Client(base_url="http://127.0.0.1:3000", timeout=45, trust_env=False)
        response = client.post("/api/auth/login", json={"username": account["username"], "password": account["password"], "trading_mode": mode})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        return client

    owner, other, other_mode = login(accounts[0], "virtual"), login(accounts[1], "virtual"), login(accounts[0], "live")
    prefix = "/api/stock/replay"

    def post(path, body):
        response = owner.post(prefix + path, json=body)
        if response.status_code != 200:
            raise RuntimeError(f"QA request failed: {response.status_code}: {response.text[:160]}")
        return response.json()

    try:
        before_future = owner.get("/api/paper/account").json()
        available = owner.get(prefix + "/availability").json()
        check("enabled_and_real_datasets", available["enabled"] and len(available["datasets"]) >= 3)
        check("stock_search_keeps_case_insensitive_pagination", any(row["code"] == "000100" for row in owner.get("/api/stock/universe?keyword=tcl&limit=500").json()))
        dataset = next(row for row in available["datasets"] if row["code"] == "000001" and row["start_date"] == "2026-09-01")
        raw = asyncio.run(raw_snapshot(dataset["id"]))
        body = {"idempotency_key": str(uuid4()), "name": "QA 一万元真实链路", "initial_cash": "10000", "start_date": "2026-09-01", "dataset_ids": [dataset["id"]]}
        view = post("/accounts", body)
        path = "/accounts/" + view["id"]
        check("custom_initial_cash_and_asof_chart", view["cash"] == "10000.00" and len(view["chart"]["bars"]) == 1 and view["chart"]["bars"][0]["time"] == "2026-09-01")
        check("create_idempotent", post("/accounts", body)["id"] == view["id"])
        command = {"idempotency_key": str(uuid4()), "expected_revision": 0, "code": "000001", "direction": "buy", "quantity": 100, "order_type": "market"}
        with ThreadPoolExecutor(max_workers=2) as executor:
            twins = list(executor.map(lambda _: post(path + "/orders", command), range(2)))
        view = twins[0]
        check("concurrent_order_idempotency", twins[0] == twins[1] and view["revision"] == 1 and len(view["orders"]) == 1 and not view["positions"])
        conflict = owner.post(prefix + path + "/orders", json={**command, "quantity": 200})
        check("idempotency_payload_conflict", conflict.status_code == 409)
        check("cross_user_and_mode_404", other.get(prefix + path).status_code == 404 and other_mode.get(prefix + path).status_code == 404)
        step = {"idempotency_key": str(uuid4()), "expected_revision": 1}
        with ThreadPoolExecutor(max_workers=2) as executor:
            twins = list(executor.map(lambda _: post(path + "/step", step), range(2)))
        view = twins[0]
        check("concurrent_step_idempotency", twins[0] == twins[1] and view["revision"] == 2 and view["current_date"] == raw[1]["trade_date"])
        buy_price = min(money(Decimal(raw[1]["open"]) * Decimal("1.001")), Decimal(raw[1]["up_limit"]))
        buy_cost = money(buy_price * 100) + fees(buy_price * 100)
        expected_cash = Decimal("10000") - buy_cost
        check("independent_raw_open_cash_fees_equity", Decimal(view["cash"]) == expected_cash and Decimal(view["fees_paid"]) == fees(buy_price * 100) and Decimal(view["equity"]) == expected_cash + Decimal(raw[1]["close"]) * 100 and Decimal(view["orders"][0]["filled_price"]) == buy_price)
        check("no_future_bars_after_step", all(bar["time"] <= view["current_date"] for bar in view["chart"]["bars"]) and len(view["chart"]["bars"]) == 2)
        check("stale_revision_rejected", owner.post(prefix + path + "/step", json={"idempotency_key": str(uuid4()), "expected_revision": 0}).status_code == 409)
        check("naked_short_rejected", owner.post(prefix + path + "/orders", json={"idempotency_key": str(uuid4()), "expected_revision": 2, "code": "000001", "direction": "sell", "quantity": 200}).status_code == 409)
        view = post(path + "/orders", {"idempotency_key": str(uuid4()), "expected_revision": 2, "code": "000001", "direction": "sell", "quantity": 100})
        check("next_open_sell_reservation", view["positions"][0]["available_quantity"] == 0 and view["orders"][0]["status"] == "pending")
        view = post(path + "/step", {"idempotency_key": str(uuid4()), "expected_revision": 3})
        sell_price = max(money(Decimal(raw[2]["open"]) * Decimal(".999")), Decimal(raw[2]["down_limit"]))
        proceeds = money(sell_price * 100) - fees(sell_price * 100, True)
        check("sell_cash_and_realized_pnl", not view["positions"] and Decimal(view["cash"]) == expected_cash + proceeds and Decimal(view["realized_pnl"]) == money(proceeds - buy_cost))
        view = post(path + "/orders", {"idempotency_key": str(uuid4()), "expected_revision": 4, "code": "000001", "direction": "buy", "quantity": 100, "order_type": "limit", "limit_price": "1.00"})
        cancel_path = path + "/orders/" + view["orders"][0]["id"] + "/cancel"
        cancel = {"idempotency_key": str(uuid4()), "expected_revision": 5}
        view = post(cancel_path, cancel)
        check("cancel_and_duplicate_release_once", view == post(cancel_path, cancel) and view["reserved_cash"] == "0.00" and view["orders"][0]["status"] == "cancelled")
        check("futures_financial_account_unchanged", owner.get("/api/paper/account").json() == before_future)
        check("bounded_pagination", owner.get(prefix + "/accounts?limit=101").status_code == 422)
        check("bad_lot_and_code_422", owner.post(prefix + path + "/orders", json={"idempotency_key": str(uuid4()), "expected_revision": 6, "code": "300001", "direction": "buy", "quantity": 1}).status_code == 422)
        print(f"{len(results)} real-chain checks passed; no AI calls; no original-user mutations")
    finally:
        for client in (owner, other, other_mode):
            client.close()


if __name__ == "__main__":
    main()
