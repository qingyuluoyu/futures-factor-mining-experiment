import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.paper.account import PaperAccount
from app.models.paper.ledger import PaperLedger
from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.models.paper.settings import PaperTradeSettings
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.models.user_notify_setting import UserNotifySetting
from app.services.paper.account_svc import deposit_custom_funds, get_account_summary
from app.services.paper.matching.matcher import _fill_one
from app.services.paper.order_query import cancel_order
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings

TABLES = (
    "users", "system_settings", "paper_accounts", "paper_ledgers", "paper_orders",
    "paper_positions", "paper_trade_settings", "user_notify_settings", "notifications",
)


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only the dedicated local PostgreSQL 5433 database is permitted")
    namespace = "qa_paper_custom_" + uuid4().hex
    users = [uuid4(), uuid4()]
    engine = create_replay_engine(settings)
    engine.sync_engine.hide_parameters = True
    factory = async_sessionmaker(engine, expire_on_commit=False)
    checks = []
    output = ROOT / "logs" / f"paper-custom-funds-postgres-results-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    report = {"namespace": namespace, "database_port": 5433, "checks": checks, "cleanup": False}

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        if not condition:
            raise AssertionError(name)

    @asynccontextmanager
    async def scoped():
        async with factory() as session:
            await session.execute(text(f'SET LOCAL search_path TO "{namespace}", public'))
            await session.execute(text("SET LOCAL statement_timeout = '15s'"))
            await session.execute(text("SET LOCAL lock_timeout = '10s'"))
            if await session.scalar(text("SELECT current_schema()")) != namespace:
                raise RuntimeError("QA schema isolation check failed")
            yield session

    async def deposit(user_id, mode, amount, key):
        async with scoped() as session:
            result = await deposit_custom_funds(session, user_id, Decimal(amount), key, mode)
            await session.commit()
            return result

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            for table in TABLES:
                await connection.execute(text(f'CREATE TABLE "{namespace}"."{table}" (LIKE public."{table}" INCLUDING ALL)'))
        async with scoped() as session:
            for index, user_id in enumerate(users):
                session.add(User(id=user_id, username=f"qa_paper_{namespace[-12:]}_{index}", hashed_password="disabled-qa-login", market_type="futures"))
                session.add(UserNotifySetting(user_id=user_id, notify_trade=False, notify_fund=False))
                session.add(PaperTradeSettings(user_id=user_id, trading_mode="virtual", fee_mode_override="fixed", open_fee_override=Decimal("1.00")))
            session.add(SystemSettings(id=1, paper_claim_amount=1, paper_total_claim_cap=1))
            account = PaperAccount(user_id=users[0], trading_mode="virtual", balance=Decimal("100.00"), frozen_margin=Decimal("200.00"), realized_pnl=Decimal("-7.50"), total_claimed=Decimal("60.00"), last_claim_month="2026-09")
            session.add(account)
            await session.flush()
            virtual_id = account.id
            session.add(PaperPosition(account_id=account.id, user_id=users[0], symbol="zn2701", direction="long", source="manual", quantity=1, available_qty=1, avg_price=100, margin=100, realized_pnl=Decimal("-7.50"), multiplier=10, margin_rate=Decimal("0.1")))
            order = PaperOrder(account_id=account.id, user_id=users[0], symbol="rb2701", direction="buy", offset="open", order_type="limit", price=100, quantity=1, multiplier=10, margin_rate=Decimal("0.1"), frozen_margin=100)
            session.add(order)
            await session.flush()
            order_id = order.id
            await session.commit()

        same_key = uuid4()
        repeated = await asyncio.wait_for(asyncio.gather(*(deposit(users[0], "live", "2000000.25", same_key) for _ in range(12))), timeout=25)
        check("twelve_postgres_deposits_credit_once", sum(not item["replayed"] for item in repeated) == 1 and all(item["balance"] == 2000000.25 for item in repeated))
        try:
            await deposit(users[0], "live", "1.00", same_key)
        except HTTPException as exc:
            check("same_key_different_amount_conflicts", exc.status_code == 409)
        else:
            check("same_key_different_amount_conflicts", False)
        virtual = await deposit(users[0], "virtual", "10.25", same_key)
        other = await deposit(users[1], "virtual", "10.25", same_key)
        check("same_key_isolated_by_user_and_mode", virtual["balance"] == 110.25 and other["balance"] == 10.25)
        check("deposit_preserves_frozen_pnl_and_legacy_history", virtual["frozen_margin"] == 200 and virtual["realized_pnl"] == -7.5 and virtual["total_claimed"] == 60 and virtual["last_claim_month"] == "2026-09")
        async with scoped() as session:
            check("repeated_key_records_one_ledger_per_scoped_account", await session.scalar(select(func.count()).select_from(PaperLedger).where(PaperLedger.ref_id == same_key)) == 3)

        gate = asyncio.Event()
        ready = asyncio.Event()
        waiting = 0

        async def overlap(is_match):
            nonlocal waiting
            async with scoped() as session:
                stale_account = await session.scalar(select(PaperAccount).where(PaperAccount.id == virtual_id))
                stale_order = await session.scalar(select(PaperOrder).where(PaperOrder.id == order_id))
                if stale_account.balance != Decimal("110.25") or stale_order.status != "pending":
                    raise AssertionError("workers must preload the same pre-execution state")
                waiting += 1
                if waiting == 12:
                    ready.set()
                await gate.wait()
                if is_match:
                    result = await _fill_one(session, stale_order, Decimal("99"), None, None)
                else:
                    await deposit_custom_funds(session, users[0], Decimal("10.25"), uuid4(), "virtual")
                    result = None
                await session.commit()
                return result

        tasks = [asyncio.create_task(overlap(index < 4)) for index in range(12)]
        try:
            await asyncio.wait_for(ready.wait(), timeout=10)
            gate.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=25)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        check("four_matchers_fill_same_order_once_during_eight_deposits", sum(result is True for result in results) == 1)
        async with scoped() as session:
            summary = await get_account_summary(session, users[0], "virtual")
            check("concurrent_postgres_cash_is_conserved", summary["balance"] == 191.25 and summary["frozen_margin"] == 200 and summary["realized_pnl"] == -7.5 and summary["total_equity"] == 391.25)
            check("custom_deposits_extend_total_deposit_without_changing_claims", summary["total_deposit"] == 152.25 and summary["total_claimed"] == 60 and summary["funding_policy"] == "custom")
            check("single_fill_has_one_fee_and_position", await session.scalar(select(func.count()).select_from(PaperLedger).where(PaperLedger.ref_type == "order", PaperLedger.ref_id == order_id, PaperLedger.flow_type == "fee")) == 1 and await session.scalar(select(PaperPosition.quantity).where(PaperPosition.account_id == virtual_id, PaperPosition.symbol == "rb2701")) == 1)
            check("virtual_custom_ledger_count_is_exact", await session.scalar(select(func.count()).select_from(PaperLedger).where(PaperLedger.account_id == virtual_id, PaperLedger.ref_type == "custom_deposit")) == 9)
            account = await session.scalar(select(PaperAccount).where(PaperAccount.id == virtual_id))
            account.balance -= Decimal("20.00")
            account.frozen_margin += Decimal("20.00")
            cancelled = PaperOrder(account_id=virtual_id, user_id=users[0], symbol="cu2701", direction="buy", offset="open", order_type="limit", price=100, quantity=1, multiplier=10, margin_rate=Decimal("0.02"), frozen_margin=20)
            session.add(cancelled)
            await session.flush()
            cancelled_id = cancelled.id
            await session.commit()
        async with scoped() as stale:
            cancelled = await stale.scalar(select(PaperOrder).where(PaperOrder.id == cancelled_id))
            async with scoped() as current:
                await cancel_order(current, users[0], cancelled_id, "virtual")
                await current.commit()
            check("stale_matcher_rechecks_cancelled_order", not await _fill_one(stale, cancelled, Decimal("99"), None, None))
            await stale.commit()
        async with scoped() as session:
            check("cancelled_order_has_no_fee_or_position", await session.scalar(select(func.count()).select_from(PaperLedger).where(PaperLedger.ref_id == cancelled_id, PaperLedger.flow_type == "fee")) == 0 and await session.scalar(select(func.count()).select_from(PaperPosition).where(PaperPosition.account_id == virtual_id, PaperPosition.symbol == "cu2701")) == 0)
            summary = await get_account_summary(session, users[0], "virtual")
            check("cancellation_and_stale_matching_preserve_cash", summary["balance"] == 191.25 and summary["frozen_margin"] == 200)

        rollback_key = uuid4()
        async with scoped() as session:
            await deposit_custom_funds(session, users[1], Decimal("11.11"), rollback_key, "live")
            await session.rollback()
        async with scoped() as session:
            check("rolled_back_deposit_removes_account_and_ledger", await session.scalar(select(func.count()).select_from(PaperLedger).where(PaperLedger.ref_id == rollback_key)) == 0 and await session.scalar(select(func.count()).select_from(PaperAccount).where(PaperAccount.user_id == users[1], PaperAccount.trading_mode == "live")) == 0)
        retry = await deposit(users[1], "live", "11.11", rollback_key)
        check("rolled_back_idempotency_key_can_be_retried", retry["balance"] == 11.11 and not retry["replayed"])
        async with engine.connect() as connection:
            check("qa_users_never_enter_public_schema", await connection.scalar(text("SELECT count(*) FROM public.users WHERE id = ANY(:ids)"), {"ids": users}) == 0)
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        original = getattr(exc, "orig", None)
        cause = getattr(original, "__cause__", None)
        report["database_error"] = {
            field: getattr(cause, field, None)
            for field in ("sqlstate", "schema_name", "table_name", "column_name", "constraint_name")
        }
        print("database_error", json.dumps(report["database_error"]), flush=True)
        raise
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SET LOCAL lock_timeout = '10s'"))
                for table in reversed(TABLES):
                    await connection.execute(text(f'DROP TABLE IF EXISTS "{namespace}"."{table}"'))
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}"'))
            async with engine.connect() as connection:
                report["cleanup"] = await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": namespace}) is None
            check("private_qa_schema_and_all_test_resources_removed", report["cleanup"])
        finally:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("report", output.name, flush=True)
            await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("FAILED", type(exc).__name__, flush=True)
        sys.exit(1)
