import asyncio
import json
import math
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import bcrypt
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock import StockInstrument
from app.models.user import User
from app.services.stock_catalog import upsert_stock_catalog
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def verify_database(username, password):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433 or not username.startswith("qa_"):
        raise RuntimeError("Only dedicated local QA database and users are permitted")
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session:
            user = await session.scalar(select(User).where(User.username == username))
            assert user and user.role == "user" and user.phone is None and user.email is None
            assert user.hashed_password != password and bcrypt.checkpw(password.encode(), user.hashed_password.encode())
            assert await session.scalar(select(func.count()).select_from(User).where(User.username == username)) == 1
            before = await session.scalar(select(func.count()).select_from(StockInstrument))
            selected = (await session.execute(select(StockInstrument).order_by(StockInstrument.code).limit(2))).scalars().all()
            rows = [{field: getattr(row, field) for field in ("code", "name", "exchange", "industry", "list_date")} for row in selected]
            await upsert_stock_catalog(session, rows)
            await upsert_stock_catalog(session, rows)
            assert await session.scalar(select(func.count()).select_from(StockInstrument)) == before
            await session.rollback()
            return before
    finally:
        await engine.dispose()


def main():
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    assert accounts[0]["username"].startswith("qa_")
    results = []

    def check(name, condition):
        results.append({"name": name, "passed": bool(condition)})
        (ROOT / "logs/stock-expansion-http-results-20261018.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    with httpx.Client(base_url=os.environ.get("QA_BASE_URL", "http://127.0.0.1:3000"), timeout=90, trust_env=False) as client:
        status = client.get("/api/auth/registration-status").json()
        check("open_registration_policy_no_quota", status["registration_policy"] == "open" and status["registration_enabled"] and status["daily_register_limit"] == 0 and status["remaining"] is None)
        check("unauthorized_market_rejected", client.get("/api/stock/market/boards").status_code in (401, 403))
        username, password = "qa_reg_" + uuid4().hex[:10], secrets.token_urlsafe(18)
        registration = {"username": username, "password": password, "trading_mode": "virtual"}
        (ROOT / "logs/qa-registration-20261018.json").write_text(json.dumps(registration), encoding="utf-8")
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: client.post("/api/auth/register", json=registration), range(2)))
        check("concurrent_registration_unique_201_409", sorted(response.status_code for response in responses) == [201, 409] and all(response.headers.get("X-Request-ID") for response in responses))
        count = asyncio.run(verify_database(username, password))
        check("hashed_password_optional_contacts_and_catalog_upsert_idempotency", count >= 5558)
        login = client.post("/api/auth/login", json=registration)
        check("new_account_can_login", login.status_code == 200)
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        check("new_account_bound_identity", client.get("/api/auth/me").json()["username"] == username)
        qa = client.post("/api/auth/login", json={**accounts[0], "trading_mode": "virtual"})
        qa.raise_for_status()
        client.headers["Authorization"] = "Bearer " + qa.json()["access_token"]
        futures_before = client.get("/api/paper/account").json()
        response = client.get("/api/stock/market/boards")
        response.raise_for_status()
        boards = response.json()
        check("real_catalog_and_sector_coverage", boards["coverage"]["instruments"] >= 5558 and boards["total"] == 110 and sum(row["count"] for row in boards["boards"]) == boards["coverage"]["instruments"])
        check("coverage_not_faked", 0 < boards["coverage"]["quoted"] < boards["coverage"]["instruments"] and all(row["change_pct"] is None for row in boards["boards"] if row["comparable_count"] == 0))
        bank = client.get("/api/stock/market/members", params={"industry": "银行", "limit": 50}).json()
        check("sector_members_standard_ids", bank["total"] > 10 and all(row["industry"] == "银行" and row["instrument_id"].endswith((".SH", ".SZ", ".BJ")) for row in bank["items"]))
        first = client.get("/api/stock/market/members", params={"limit": 50, "offset": 0}).json()
        second = client.get("/api/stock/market/members", params={"limit": 50, "offset": 50}).json()
        check("pagination_no_duplicates", len(first["items"]) == 50 and len(second["items"]) == 50 and not ({row["code"] for row in first["items"]} & {row["code"] for row in second["items"]}))
        search = client.get("/api/stock/market/members", params={"keyword": "tcl"}).json()
        check("case_insensitive_stock_search", any(row["code"] == "000100" for row in search["items"]))
        missing = next(row for row in first["items"] if not row["data_available"])
        detail = client.get("/api/stock/market/details", params={"code": missing["code"]}).json()
        check("catalog_only_has_no_fake_quote", missing["close"] is None and detail["coverage"]["daily_rows"] == 0)
        detail = client.get("/api/stock/market/details?code=600519").json()
        check("real_stock_details_disclosure_dates", detail["coverage"]["daily_rows"] > 100 and all(row["publish_date"] <= detail["as_of"] and row["report_date"] <= detail["as_of"] for row in detail["financials"]))
        check("strict_market_input_and_not_found", client.get("/api/stock/market/members?limit=101").status_code == 422 and client.get("/api/stock/market/details?code=999999").status_code == 404)
        body = {"factor_ids": ["ret_20d"], "start_date": "20260101", "end_date": "20260930", "rebalance": "month", "top_n": 5}
        backtest = client.post("/api/stock/backtest", json=body)
        check("real_research_nav_and_method", backtest.status_code == 200 and bool(backtest.json().get("nav")) and all(math.isfinite(row["nav"]) and row["nav"] > 0 for row in backtest.json()["nav"]) and backtest.json().get("method_version") == "stock-research-2026-10-18.v3")
        analysis_body = {"factor_id": "ret_20d", "start_date": "20260101", "end_date": "20260930", "groups": 5}
        missing_price = client.post("/api/stock/factors/analysis", json=analysis_body)
        check("real_missing_held_price_rejected_not_filled", missing_price.status_code == 409 and missing_price.headers.get("X-Research-Error") == "missing_held_price" and bool(missing_price.headers.get("X-Request-ID")))
        analysis = client.post("/api/stock/factors/analysis", json={**analysis_body, "start_date": "20260713", "end_date": "20260911"})
        check("real_factor_analysis_and_risk_disclosure", analysis.status_code == 200 and bool(analysis.json().get("validation_limits")) and analysis.headers.get("X-Method-Version") == "stock-research-2026-10-18.v3")
        check("research_invalid_enum_not_silent_fallback", client.post("/api/stock/backtest", json={**body, "rebalance": "daily_typo"}).status_code == 422)
        check("futures_account_unchanged_by_stock_reads_research", client.get("/api/paper/account").json() == futures_before)
    print(json.dumps({"checks": len(results), "passed": sum(row["passed"] for row in results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
