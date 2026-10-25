import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.ai_chat import router as chat_router
from app.api.ai_tools import router as tools_router
from app.api.stock import router as stock_router
from app.core.auth import create_access_token
from app.core.deps import get_session, get_settings
from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.paper.account import PaperAccount
from app.models.paper.ledger import PaperLedger
from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.models.stock import StockFinancial, StockValuation
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.services.ai import gateway
from app.services.ai.providers.base import StreamEvent
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main(*, additional_checks=None, report_label="b02"):
    if report_label not in {"b02", "b03", "b04", "b05", "chat", "night", "dual", "b08", "b09", "b10", "b11", "b12", "b13", "b14", "b15", "b16", "b17", "b18", "b19", "b20", "b21", "b22", "b23"}:
        raise ValueError("Invalid report label")
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 permitted")
    database = create_replay_engine(settings)
    factory = async_sessionmaker(database, expire_on_commit=False)
    namespace = "qa_completion_b02_" + uuid4().hex
    if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
        raise RuntimeError("Invalid isolated namespace")
    report = {"status": "running", "checks": [], "source_calls": 0, "paid_model_calls": 0,
              "public_business_writes": 0, "data_scope": "synthetic_private_schema_fixtures_not_market_certification"}
    output = ROOT / "logs" / f"stock-completion-{report_label}-pg-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    created = False
    wanted = "paper_list_positions"

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(name, "PASS" if passed else "FAIL", flush=True)
        if not passed:
            raise AssertionError(name)

    @asynccontextmanager
    async def scoped():
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{namespace}", public'))
            await session.execute(text("SET statement_timeout = '10s'"))
            yield session

    async def dependency():
        async with scoped() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    class FixtureProvider:
        def __init__(self):
            self.calls = 0

        async def chat_stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(type="tool_call", tool_name=wanted, tool_call_id="fixture-call", tool_args=json.dumps({"confirmed": True, "user_id": str(other_id), "trading_mode": "live"}))
            else:
                yield StreamEvent(type="message", content="隔离验证结束")

    def tool_result(response):
        for block in response.text.split("\n\n"):
            if block.startswith("event: tool_result"):
                data = json.loads(next(line[6:] for line in block.splitlines() if line.startswith("data: ")))
                return json.loads(data["content"])
        raise AssertionError("Missing tool result")

    def headers(user_id, mode="virtual"):
        return {"Authorization": "Bearer " + create_access_token(settings, str(user_id), "user", mode)}

    try:
        async with asyncio.timeout(90):
            async with database.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
                created = True
                for table in ("users", "ai_models", "ai_providers", "paper_accounts", "paper_positions", "paper_orders", "paper_ledgers", "system_settings", "stock_valuation", "stock_financial"):
                    await connection.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
                if additional_checks is not None:
                    for table in ("stock_research_jobs", "stock_research_schema_versions"):
                        await connection.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
            async with scoped() as session:
                owner = User(username="qa_completion_b02_owner", hashed_password="non-login-fixture", market_type="futures")
                other = User(username="qa_completion_b02_other", hashed_password="non-login-fixture", market_type="futures")
                stock_user = User(username="qa_completion_b02_stock", hashed_password="non-login-fixture", market_type="stock")
                session.add_all([owner, other, stock_user, SystemSettings(id=1)])
                await session.flush()
                owner_id, other_id, stock_id = owner.id, other.id, stock_user.id
                accounts = []
                for user_id, mode, balance, quantity in ((owner_id, "live", "1111", 1), (owner_id, "virtual", "7777", 7), (other_id, "virtual", "9999", 9)):
                    account = PaperAccount(user_id=user_id, trading_mode=mode, balance=Decimal(balance))
                    session.add(account)
                    await session.flush()
                    accounts.append(account)
                    session.add(PaperPosition(user_id=user_id, account_id=account.id, symbol="rb2701", direction="long", quantity=quantity, available_qty=quantity, avg_price=Decimal("3000")))
                    session.add(PaperOrder(user_id=user_id, account_id=account.id, symbol="rb2701", direction="buy", offset="open", order_type="limit", price=Decimal("3000"), quantity=quantity, status="pending"))
                provider = AIProvider(user_id=owner_id, name="isolated-fixture", api_type="openai", base_url="https://unused.invalid", api_key="synthetic-non-secret")
                session.add(provider)
                await session.flush()
                session.add(AIModel(user_id=owner_id, provider_id=provider.id, model_id="qa-b02", display_name="isolated-fixture"))
                session.add_all([StockValuation(id=index + 1, code="600519", trade_date=date(2026, 10, 10) + timedelta(days=index), pe_ttm=None if index == 5 else float(index)) for index in range(6)])
                session.add_all([StockFinancial(id=index + 1, code="600519", report_date=date(2026, (index + 1) * 3, 28), publish_date=None, roe=float(index)) for index in range(3)])
                await session.commit()
                check("all_tables_resolve_only_to_private_schema", await session.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:namespace AND c.relname='paper_accounts'"), {"namespace": namespace}) == 1)
            application = FastAPI()
            for router in (chat_router, tools_router, stock_router):
                application.include_router(router)
            application.state.session_factory = scoped
            application.dependency_overrides[get_session] = dependency
            application.dependency_overrides[get_settings] = lambda: settings
            with patch.object(gateway, "decrypt", return_value="synthetic-non-secret"), patch.object(gateway, "create_provider", side_effect=lambda *args, **kwargs: FixtureProvider()), patch.object(gateway, "load_skill_tools", new_callable=AsyncMock, return_value=[]):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://isolated", timeout=20) as client:
                    check("chat_requires_jwt", (await client.post("/api/ai/chat", json={"model_id": "qa-b02"})).status_code == 401)
                    for mode, expected_quantity, expected_balance in (("live", 1, 1111), ("virtual", 7, 7777)):
                        for name in ("paper_list_positions", "paper_list_orders", "paper_get_account"):
                            wanted = name
                            response = await client.post("/api/ai/chat", headers=headers(owner_id, mode), json={"model_id": "qa-b02", "messages": [], "trading_mode": "live" if mode == "virtual" else "virtual"})
                            payload = tool_result(response)
                            check(f"real_chat_{mode}_{name}_200", response.status_code == 200 and payload.get("success") is True)
                            if name == "paper_get_account":
                                check(f"real_chat_{mode}_own_balance_only", payload["account"]["balance"] == expected_balance)
                            else:
                                check(f"real_chat_{mode}_{name}_own_quantity_only", len(payload["items"]) == 1 and payload["items"][0]["quantity"] == expected_quantity)
                            check(f"real_chat_{mode}_{name}_trace_and_done", bool(response.headers.get("X-Request-ID")) and "event: done" in response.text and "event: error" not in response.text)
                    for wanted in ("paper_open_long", "paper_open_short", "paper_place_limit", "paper_close_position", "paper_cancel_order", "code_sandbox"):
                        response = await client.post("/api/ai/chat", headers=headers(owner_id), json={"model_id": "qa-b02"})
                        check(f"model_confirmed_cannot_execute_{wanted}", tool_result(response).get("error_code") == "server_confirmation_required" and "event: confirmation_request" not in response.text)
                    check("stock_chat_refuses_futures_agent_409", (await client.post("/api/ai/chat", headers=headers(stock_id), json={"model_id": "qa-b02"})).status_code == 409)
                    other_model = await client.post("/api/ai/chat", headers=headers(other_id), json={"model_id": "qa-b02"})
                    check("other_owner_cannot_use_model", "模型未找到" in other_model.text and "event: tool_result" not in other_model.text)
                    tools = (await client.get("/api/ai/tools", headers=headers(owner_id))).json()["items"]
                    check("virtual_unadapted_or_write_tools_remain_unavailable", all(not tool["enabled"] for tool in tools if tool["name"] in ("paper_open_long", "research_products")))
                    check("free_trade_cannot_reenable_write_409", (await client.put("/api/ai/tools/paper_open_long/free-trade", headers=headers(owner_id), json={"free_trade": True})).status_code == 409)
                    check("valuation_requires_jwt", (await client.get("/api/stock/valuation?code=600519")).status_code == 401)
                    check("valuation_invalid_date_422", (await client.get("/api/stock/valuation?code=600519&start=invalid", headers=headers(stock_id))).status_code == 422)
                    check("valuation_reversed_dates_422", (await client.get("/api/stock/valuation?code=600519&start=2026-10-16&end=2026-10-01", headers=headers(stock_id))).status_code == 422)
                    pages = []
                    for offset in (0, 2, 4):
                        response = await client.get(f"/api/stock/valuation?code=600519&end=2026-10-16&limit=2&offset={offset}", headers=headers(stock_id))
                        pages.extend(row["trade_date"] for row in response.json())
                        check(f"valuation_page_{offset}_bounded", len(response.json()) == 2 and response.headers["X-Page-Has-More"] == str(offset < 4).lower())
                    check("valuation_page_union_exact_without_duplicates", len(pages) == len(set(pages)) == 6)
                    last = await client.get("/api/stock/valuation?code=600519&limit=1", headers=headers(stock_id))
                    check("valuation_missing_pe_stays_null", last.json()[0]["pe_ttm"] is None)
                    metadata = json.loads(last.headers["X-Data-Metadata"])
                    check("legacy_source_not_falsely_certified", metadata["provenance"] == "legacy_unverified" and metadata["point_in_time_certified"] is False)
                    financial = await client.get("/api/stock/financials?code=600519&limit=2", headers=headers(stock_id))
                    check("financial_page_bounded_and_null_disclosure_preserved", len(financial.json()) == 2 and all(row["publish_date"] is None for row in financial.json()) and financial.headers["X-Page-Has-More"] == "true")
                    if additional_checks is not None:
                        await additional_checks(check=check, scoped=scoped, application=application, client=client, headers=headers, owner_id=owner_id, other_id=other_id, stock_id=stock_id, settings=settings)
            async with scoped() as session:
                check("chat_did_not_write_orders_or_ledgers", await session.scalar(select(func.count()).select_from(PaperOrder)) == 3 and await session.scalar(select(func.count()).select_from(PaperLedger)) == 0)
                balances = (await session.execute(select(PaperAccount.balance).order_by(PaperAccount.balance))).scalars().all()
                check("all_three_accounts_balances_unchanged", balances == [Decimal("1111"), Decimal("7777"), Decimal("9999")])
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        original = getattr(error, "orig", None)
        report["error_sqlstate"] = getattr(original, "sqlstate", None)
        identifier = re.search(r'(?:type|column|relation) "([a-z_]+)"', str(original))
        if identifier:
            report["error_identifier"] = identifier.group(1)
    finally:
        if created:
            try:
                async with database.begin() as connection:
                    await connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                    await connection.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
                report["isolated_schema_cleaned"] = True
            except Exception as error:
                report.update(status="failed", cleanup_error_type=type(error).__name__, retained_qa_schema=namespace)
        await database.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print("report", output.name, "status", report["status"])
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
