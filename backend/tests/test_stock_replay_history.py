from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import importlib
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.stock_replay import ReplayBase, StockReplayAccount, StockReplayDataset, StockReplayOrder
from app.models.user import User
from app.services.stock_replay_engine import ReplayError
from tests.test_stock_research_snapshots import snapshot_database


def modules():
    return importlib.import_module("app.schemas.stock_replay_history"), importlib.import_module("app.services.stock_replay_history")


@asynccontextmanager
async def history_database():
    async with snapshot_database() as (factory, database):
        User.__table__.create(database)
        ReplayBase.metadata.create_all(database)
        owner = uuid4()
        with Session(database) as session:
            session.add(User(id=owner, username="private-history-fixture", hashed_password="not-a-login"))
            dataset = StockReplayDataset(version="d" * 64, code="000001", instrument_id="000001.SZ", name="历史夹具", start_date=date(2026, 9, 1), end_date=date(2026, 9, 3), bars_json=[], meta={"supplier": "private_fixture"})
            session.add(dataset)
            session.flush()
            account = StockReplayAccount(user_id=owner, trading_mode="virtual", name="历史账户", initial_cash=10000, cash=10000, start_date=date(2026, 9, 1), current_date=date(2026, 9, 3), end_date=date(2026, 9, 3), datasets_json={"000001": str(dataset.id)}, positions_json=[], settings_json={}, status="completed", revision=210, create_key=uuid4(), params_hash="a" * 64, rules_version="stock-replay-v1")
            session.add(account)
            session.flush()
            for index in range(210):
                session.add(StockReplayOrder(account_id=account.id, idempotency_key=uuid4(), params_hash="b" * 64, code="000001", direction="buy" if index % 2 else "sell", quantity=100, order_type="market", status="rejected", reason="liquidity_limit", submitted_on=date(2026, 9, 1) + timedelta(days=index % 2), reserved_cash=0, created_at=datetime(2026, 9, 2, tzinfo=timezone.utc) + timedelta(seconds=index)))
            session.commit()
            account_id = account.id
        yield factory, database, owner, account_id


@pytest.mark.parametrize("patch", [{"expected_revision": -1}, {"expected_revision": 2147483648}, {"status": "all;drop"}, {"direction": "short"}, {"code": "300001"}, {"code": "０００００１"}, {"start": "20260901"}, {"start": "2026-02-30"}, {"start": "2026-09-02", "end": "2026-09-01"}, {"limit": 101}, {"offset": -1}, {"extra": True}])
def test_order_history_query_is_strict(patch):
    schema, _ = modules()
    with pytest.raises(ValidationError):
        schema.ReplayHistoryQuery(**{"expected_revision": 210, **patch})


@pytest.mark.asyncio
async def test_read_all_210_orders_in_bounded_pages_with_exact_filters_and_readonly_queries():
    schema, service = modules()
    async with history_database() as (factory, database, owner, account_id):
        statements = []
        event.listen(database, "before_cursor_execute", lambda connection, cursor, statement, parameters, context, many: statements.append(statement))
        query = schema.ReplayHistoryQuery(expected_revision=210, limit=100)
        pages = [await service.read_order_history(factory, owner, "virtual", account_id, query.model_copy(update={"offset": offset})) for offset in (0, 100, 200)]
        assert [len(page["items"]) for page in pages] == [100, 100, 10]
        assert [page["has_more"] for page in pages] == [True, True, False]
        assert len({item["id"] for page in pages for item in page["items"]}) == 210
        assert all(page["total"] == 210 and page["revision"] == 210 for page in pages)
        filtered = await service.read_order_history(factory, owner, "virtual", account_id, schema.ReplayHistoryQuery(expected_revision=210, status="rejected", direction="buy", start="2026-09-02", end="2026-09-02", code="000001"))
        assert filtered["total"] == 105 and all(item["direction"] == "buy" and item["submitted_on"] == "2026-09-02" for item in filtered["items"])
        assert filtered["items"][0]["cash_delta"] is None and filtered["items"][0]["fees"] == "0.00"
        assert filtered["items"][0]["instrument_id"] == "000001.SZ"
        assert filtered["point_in_time_certified"] is False and filtered["data_version"] is None
        assert filtered["transaction"] == "sqlite_explicit_select_only"
        assert len([statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]) == 12
        assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)


@pytest.mark.asyncio
async def test_owner_mode_revision_and_future_cursor_enforced_not_empty_success():
    schema, service = modules()
    async with history_database() as (factory, _, owner, account_id):
        query = schema.ReplayHistoryQuery(expected_revision=210)
        for user_id, mode in ((uuid4(), "virtual"), (owner, "live")):
            with pytest.raises(ReplayError) as error:
                await service.read_order_history(factory, user_id, mode, account_id, query)
            assert error.value.status == 404
        for patch, expected in (({"expected_revision": 209}, 409), ({"end": date(2026, 9, 4)}, 422), ({"code": "600001"}, 422)):
            with pytest.raises(ReplayError) as error:
                await service.read_order_history(factory, owner, "virtual", account_id, query.model_copy(update=patch))
            assert error.value.status == expected


@pytest.mark.asyncio
async def test_stored_filled_money_and_dates_are_validated_not_silently_repaired():
    schema, service = modules()
    async with history_database() as (factory, database, owner, account_id):
        with Session(database) as session:
            order = session.scalars(select(StockReplayOrder).order_by(StockReplayOrder.created_at.desc())).first()
            order.status, order.filled_on, order.filled_price = "filled", date(2026, 9, 3), Decimal("10")
            order.cash_delta, order.fees, order.realized_pnl = Decimal("-1005.01"), Decimal("5.01"), Decimal("-1.23")
            session.commit()
        result = await service.read_order_history(factory, owner, "virtual", account_id, schema.ReplayHistoryQuery(expected_revision=210, limit=1))
        assert result["items"][0]["filled_price"] == "10.0000"
        assert result["items"][0]["cash_delta"] == "-1005.01"
        with Session(database) as session:
            order = session.get(StockReplayOrder, UUID(result["items"][0]["id"]))
            order.filled_on = date(2026, 9, 4)
            session.commit()
        with pytest.raises(ReplayError) as error:
            await service.read_order_history(factory, owner, "virtual", account_id, schema.ReplayHistoryQuery(expected_revision=210, limit=1))
        assert error.value.code == "invalid_order_history"
