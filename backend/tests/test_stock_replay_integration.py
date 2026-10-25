from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.stock_replay import ReplayBase, StockReplayDataset, StockReplayOrder
from app.models.user import User
from app.schemas.stock_replay import CreateReplayAccount, ReplayMutation, SubmitReplayOrder
from app.services.stock_replay import StockReplayService
from app.services.stock_replay_engine import ReplayError


@asynccontextmanager
async def replay_database(*, changed_factor=False, missing_day=False):
    engine = create_engine("sqlite:///:memory:")
    User.__table__.create(engine)
    ReplayBase.metadata.create_all(engine)

    class DatabaseSession:
        def __init__(self):
            self.sync = Session(engine, expire_on_commit=False)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.sync.close()

        def add(self, model):
            self.sync.add(model)

        async def execute(self, statement):
            return self.sync.execute(statement)

        async def scalar(self, statement):
            return self.sync.scalar(statement)

        async def flush(self):
            self.sync.flush()

        async def commit(self):
            self.sync.commit()

        async def rollback(self):
            self.sync.rollback()

    factory = DatabaseSession
    user_id = uuid4()
    async with factory() as session:
        session.add(User(id=user_id, username="qa_replay_fixture", hashed_password="test-only-not-a-login"))
        bars = [{"trade_date": f"2026-09-0{day}", "open": "10", "high": "11", "low": "9", "close": "11", "volume_shares": 100000, "adj_factor": "2" if changed_factor and day == 2 else "1", "up_limit": "12", "down_limit": "8"} for day in (1, 2, 3)]
        if missing_day:
            bars.pop(1)
        dataset = StockReplayDataset(version="a" * 64, code="000001", instrument_id="000001.SZ", name="测试夹具", start_date=date(2026, 9, 1), end_date=date(2026, 9, 3), bars_json=bars, meta={"supplier": "test_fixture", "calendar_dates": [f"2026-09-0{day}" for day in (1, 2, 3)]})
        session.add(dataset)
        await session.commit()
        dataset_id = dataset.id
    try:
        yield factory, user_id, dataset_id
    finally:
        engine.dispose()


async def create_account(session, user_id, dataset_id):
    service = StockReplayService(session, user_id, "virtual")
    body = CreateReplayAccount(idempotency_key=uuid4(), name="自填一万元", initial_cash="10000", start_date="2026-09-01", dataset_ids=[dataset_id], slippage_rate="0")
    result = await service.create(body)
    assert (await service.create(body))["id"] == result["id"]
    await session.commit()
    return service, result


@pytest.mark.asyncio
async def test_cash_orders_positions_equity_idempotency_and_isolation():
    async with replay_database() as (factory, user_id, dataset_id), factory() as session:
        service, view = await create_account(session, user_id, dataset_id)
        account_id = UUID(view["id"])
        assert view["cash"] == "10000.00" and len(view["chart"]["bars"]) == 1
        body = SubmitReplayOrder(idempotency_key=uuid4(), expected_revision=0, code="000001", direction="buy", quantity=100)
        view = await service.submit(account_id, body)
        assert view["revision"] == 1 and view["positions"] == [] and view["orders"][0]["status"] == "pending"
        assert (await service.submit(account_id, body))["revision"] == 1
        await session.commit()
        step = ReplayMutation(idempotency_key=uuid4(), expected_revision=1)
        view = await service.mutate(account_id, step)
        assert view["current_date"] == "2026-09-02" and view["revision"] == 2
        assert view["cash"] == "8994.99" and view["equity"] == "10094.99" and view["fees_paid"] == "5.01"
        assert view["positions"][0]["quantity"] == 100 and view["positions"][0]["available_quantity"] == 100
        assert len(view["chart"]["bars"]) == 2
        assert await service.mutate(account_id, step) == view
        await session.commit()
        for other_user, mode in [(uuid4(), "virtual"), (user_id, "live")]:
            with pytest.raises(ReplayError) as error:
                await StockReplayService(session, other_user, mode).account(account_id)
            assert error.value.status == 404
        with pytest.raises(ReplayError, match="已变化"):
            await service.mutate(account_id, ReplayMutation(idempotency_key=uuid4(), expected_revision=0))
        view = await service.submit(account_id, SubmitReplayOrder(idempotency_key=uuid4(), expected_revision=2, code="000001", direction="sell", quantity=100))
        view = await service.mutate(account_id, ReplayMutation(idempotency_key=uuid4(), expected_revision=3))
        assert view["status"] == "completed" and view["positions"] == []
        assert view["cash"] == "9989.48" and view["realized_pnl"] == "-10.52"
        await session.commit()
        assert len((await session.execute(select(StockReplayOrder))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_cancel_releases_reserved_cash_and_is_idempotent():
    async with replay_database() as (factory, user_id, dataset_id), factory() as session:
        service, view = await create_account(session, user_id, dataset_id)
        account_id = UUID(view["id"])
        view = await service.submit(account_id, SubmitReplayOrder(idempotency_key=uuid4(), expected_revision=0, code="000001", direction="buy", quantity=100, order_type="limit", limit_price="9"))
        assert Decimal(view["reserved_cash"]) > 900
        cancel = ReplayMutation(idempotency_key=uuid4(), expected_revision=1)
        view = await service.mutate(account_id, cancel, cancel_id=UUID(view["orders"][0]["id"]))
        assert view["reserved_cash"] == "0.00" and view["available_cash"] == "10000.00"
        assert view["orders"][0]["status"] == "cancelled" and view["revision"] == 2
        assert await service.mutate(account_id, cancel, cancel_id=UUID(view["orders"][0]["id"])) == view


@pytest.mark.asyncio
@pytest.mark.parametrize("options,error_code", [({"changed_factor": True}, "corporate_action_unhandled"), ({"missing_day": True}, "missing_bar")])
async def test_failure_rolls_back_cursor_order_cash_and_positions(options, error_code):
    async with replay_database(**options) as (factory, user_id, dataset_id), factory() as session:
        service, view = await create_account(session, user_id, dataset_id)
        account_id = UUID(view["id"])
        await service.submit(account_id, SubmitReplayOrder(idempotency_key=uuid4(), expected_revision=0, code="000001", direction="buy", quantity=100))
        await session.commit()
        with pytest.raises(ReplayError) as error:
            await service.mutate(account_id, ReplayMutation(idempotency_key=uuid4(), expected_revision=1))
        assert error.value.code == error_code
        await session.rollback()
        view = await service.view(await service.account(account_id))
        assert view["cash"] == "10000.00" and view["positions"] == [] and view["revision"] == 1
        assert view["orders"][0]["status"] == "pending" and view["current_date"] == "2026-09-01"


@pytest.mark.asyncio
async def test_old_pending_order_always_visible_beyond_history_window():
    async with replay_database() as (factory, user_id, dataset_id), factory() as session:
        service, view = await create_account(session, user_id, dataset_id)
        account_id = UUID(view["id"])
        view = await service.submit(account_id, SubmitReplayOrder(idempotency_key=uuid4(), expected_revision=0, code="000001", direction="buy", quantity=100, order_type="limit", limit_price="1"))
        pending_id = view["orders"][0]["id"]
        for index in range(201):
            session.add(StockReplayOrder(account_id=account_id, idempotency_key=uuid4(), params_hash=f"{index:064d}", code="000001", direction="buy", quantity=100, order_type="market", status="cancelled", submitted_on=date(2026, 9, 1)))
        await session.flush()
        view = await service.view(await service.account(account_id))
        assert len(view["orders"]) == 201
        assert any(order["id"] == pending_id and order["status"] == "pending" for order in view["orders"])
