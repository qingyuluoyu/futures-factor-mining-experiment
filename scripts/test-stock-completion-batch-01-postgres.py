import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.core.auth import create_access_token
from app.core.deps import get_session, get_settings
from app.models.ai_trading.task import AITradingTask
from app.models.user import User
from app.schemas.stock_research_jobs import CreateResearchJob
from app.services.ai_trading import engine as trading_engine
from app.services.paper.price_band import resolve_last_price
from app.services.stock_research_jobs import ResearchJobService
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


class ModelBoundaryReached(BaseException):
    pass


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 permitted")
    database = create_replay_engine(settings)
    race_enabled = False
    race_count = 0
    race_ready = asyncio.Event()

    class BarrierSession(AsyncSession):
        async def commit(self):
            nonlocal race_count
            if race_enabled and any(isinstance(row, User) and row.email == "race@example.test" for row in self.dirty):
                race_count += 1
                if race_count == 2:
                    race_ready.set()
                await asyncio.wait_for(race_ready.wait(), timeout=5)
            return await super().commit()

    factory = async_sessionmaker(database, class_=BarrierSession, expire_on_commit=False)
    namespace = "qa_completion_" + uuid4().hex
    if not re.fullmatch(r"qa_completion_[a-f0-9]{32}", namespace):
        raise RuntimeError("Invalid isolated namespace")
    report = {"checks": [], "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
              "quote_scope": "explicit_synthetic_test_fixtures_only", "status": "running"}
    output = ROOT / "logs" / f"stock-completion-b01-pg-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    created = False

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        if not condition:
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

    try:
        async with asyncio.timeout(90):
            async with database.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
                created = True
                for table in ("users", "stock_research_jobs", "ai_trading_tasks"):
                    await connection.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
            async with scoped() as session:
                owner = User(username="qa_completion_owner", email="owner@example.test", hashed_password="non-login-test-fixture", market_type="stock")
                other = User(username="qa_completion_other", email="other@example.test", phone="fixture-other", hashed_password="non-login-test-fixture", market_type="futures")
                session.add_all([owner, other])
                await session.commit()
                owner_id, other_id = owner.id, other.id
            application = FastAPI()
            application.include_router(auth_router)
            application.include_router(users_router)
            application.dependency_overrides[get_session] = dependency
            application.dependency_overrides[get_settings] = lambda: settings
            headers = {"Authorization": "Bearer " + create_access_token(settings, str(owner_id), "user", "virtual")}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://isolated", timeout=20) as client:
                check("profile_no_identity_401", (await client.put("/api/users/me", json={"email": "x@example.test"})).status_code == 401)
                response = await client.put("/api/users/me", headers=headers, json={"email": "saved@example.test", "role": "admin", "id": str(other_id)})
                payload = response.json()
                check("profile_real_http_write_200", response.status_code == 200)
                check("profile_keeps_owner_and_role", payload.get("id") == str(owner_id) and payload.get("role") == "user")
                check("profile_keeps_virtual_mode_and_stock_market", payload.get("trading_mode") == "virtual" and payload.get("market_type") == "stock")
                fresh = await client.get("/api/auth/me", headers=headers)
                check("profile_persisted_and_reread", fresh.status_code == 200 and fresh.json().get("email") == "saved@example.test")
                collision = await client.put("/api/users/me", headers=headers, json={"email": "other@example.test", "phone": "not-committed"})
                check("profile_email_conflict_409", collision.status_code == 409)
                check("profile_phone_conflict_409", (await client.put("/api/users/me", headers=headers, json={"phone": "fixture-other"})).status_code == 409)
                check("profile_invalid_email_422", (await client.put("/api/users/me", headers=headers, json={"email": "invalid-email"})).status_code == 422)
            async with scoped() as session:
                persisted = await session.get(User, owner_id)
                untouched = await session.get(User, other_id)
                check("profile_conflict_transaction_rolls_back", persisted.email == "saved@example.test" and persisted.phone is None)
                check("profile_other_user_unchanged", untouched.email == "other@example.test" and untouched.market_type == "futures")
                check("profile_only_private_schema_used", await session.scalar(text("SELECT n.nspname FROM users u JOIN pg_class c ON c.oid=u.tableoid JOIN pg_namespace n ON n.oid=c.relnamespace LIMIT 1")) == namespace)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://isolated", timeout=20) as client:
                race_enabled = True
                other_headers = {"Authorization": "Bearer " + create_access_token(settings, str(other_id), "user", "virtual")}
                raced = await asyncio.gather(client.put("/api/users/me", headers=headers, json={"email": "race@example.test"}),
                                             client.put("/api/users/me", headers=other_headers, json={"email": "race@example.test"}), return_exceptions=True)
                report["concurrent_error_types"] = [type(response).__name__ for response in raced if isinstance(response, BaseException)]
                print("concurrent_error_types", report["concurrent_error_types"])
                check("profile_concurrent_unique_collision_is_200_and_409", sorted(getattr(response, "status_code", 0) for response in raced) == [200, 409])
                check("profile_write_and_conflict_are_traced_no_store", all(len(response.headers.get("X-Request-ID", "")) == 32 and response.headers.get("Cache-Control") == "no-store" for response in raced))
            async with scoped() as session:
                parameters = {"factor_id": "ret_20d", "start_date": "20260713", "end_date": "20260911", "groups": 10,
                              "preprocess": {"winsorize": "none", "standardize": False, "fillna": False, "industry_neutral": True, "mktcap_neutral": True}}
                body = TypeAdapter(CreateResearchJob).validate_python({"kind": "analysis", "idempotency_key": str(uuid4()), "parameters": parameters})
                service = ResearchJobService(session, owner_id, "virtual")
                saved = await service.create(body, request_id=uuid4().hex)
                await session.commit()
            async with scoped() as session:
                service = ResearchJobService(session, owner_id, "virtual")
                restored = await service.get(UUID(saved["id"]))
                check("research_ten_groups_persisted", restored["params"]["groups"] == 10)
                check("research_all_preprocess_values_persisted", restored["params"]["preprocess"] == parameters["preprocess"])
                repeated = await service.create(body, request_id=uuid4().hex)
                check("research_creation_stays_idempotent", repeated["id"] == saved["id"])
                await session.commit()
            for mode in ("live", "virtual"):
                async with scoped() as session:
                    task = AITradingTask(user_id=owner_id, trading_mode=mode, name="qa_completion_mode", symbol="rb2701", symbol_name="isolated-fixture",
                                         timeframe="5m", status="running", strategy_type="ai", strategy_params={}, close_rules={}, stop_rules={},
                                         extra_timeframes=["15m", "60m"], fixed_qty=1, equity_baseline=Decimal("100000"))
                    session.add(task)
                    await session.commit()
                    task_id = task.id
                async with scoped() as session:
                    task = await session.get(AITradingTask, task_id)
                    prices = {"live": 100.0, "virtual": 500.0}
                    calls, captured = [], {}

                    class FixtureRedis:
                        async def get(self, key):
                            for kind, price in prices.items():
                                if key == f"market:{kind}:quote:rb2701":
                                    return json.dumps({"last_price": price})
                            return None

                    async def bars(*args, trading_mode="live", **kwargs):
                        calls.append((args[2], trading_mode))
                        return [{"time": f"2026-10-16 09:{index:02d}:00", "open": prices[trading_mode], "high": prices[trading_mode], "low": prices[trading_mode], "close": prices[trading_mode], "volume": 100} for index in range(40)]

                    async def boundary(session, task, context):
                        captured.update(context)
                        raise ModelBoundaryReached()

                    with patch.object(trading_engine, "get_session_status", return_value={"is_open": True}), patch.object(trading_engine, "get_kline_history", bars), \
                         patch.object(trading_engine, "find_symbol_position", AsyncMock(return_value=None)), \
                         patch.object(trading_engine, "get_account_summary", AsyncMock(return_value={"total_equity": 100000, "available_margin": 100000})), \
                         patch.object(trading_engine, "check_hard_rules", return_value=None), patch.object(trading_engine, "_check_exit_standards", return_value=None), \
                         patch.object(trading_engine, "call_model_decision", boundary):
                        try:
                            await trading_engine.evaluate_task(session, task, FixtureRedis(), force=True)
                        except ModelBoundaryReached:
                            pass
                        else:
                            raise AssertionError("Model boundary not reached")
                    check(f"persisted_{mode}_task_all_timeframes_scoped", calls == [("5m", mode), ("15m", mode), ("60m", mode)])
                    check(f"persisted_{mode}_task_real_reference_reader_scoped", captured.get("last_price") == prices[mode])
                    await session.rollback()
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        if created:
            try:
                async with database.begin() as connection:
                    await connection.execute(text('SET LOCAL lock_timeout = \'2s\''))
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
