import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.paper.account import PaperAccount
from app.models.paper.ledger import PaperLedger
from app.models.paper.order import PaperOrder
from app.models.paper.position import PaperPosition
from app.models.paper.settings import PaperTradeSettings
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.models.user_notify_setting import UserNotifySetting
from app.services.paper.account_svc import deposit_custom_funds
from app.services.paper.matching import matcher


class MatchingSession:
    def __init__(self, database):
        self.session = Session(database, expire_on_commit=False)
        self.bind = database
        self.accounts = []

    def get_bind(self):
        return self.bind

    async def execute(self, statement, params=None):
        result = self.session.execute(statement, params or {})
        if statement.is_select and getattr(statement, "_for_update_arg", None) is not None and "paper_accounts" in str(statement):
            self.accounts.append(result.scalar_one().user_id)
            result = self.session.execute(statement, params or {})
        return result

    def add(self, value):
        self.session.add(value)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.session.close()


@pytest.fixture
def matching_database(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'matching.sqlite'}")
    for model in (User, SystemSettings, PaperAccount, PaperLedger, PaperPosition, PaperOrder, PaperTradeSettings, UserNotifySetting):
        model.__table__.create(database)
    users = [UUID("bbbbbbbb-0000-0000-0000-000000000020"), UUID("aaaaaaaa-0000-0000-0000-000000000010")]
    with Session(database) as session:
        session.add(SystemSettings(id=1))
        for index, user_id in enumerate(users):
            session.add(User(id=user_id, username=f"matching-{index}", hashed_password="unused"))
            account = PaperAccount(user_id=user_id, trading_mode="virtual", balance=Decimal("100.00"), frozen_margin=Decimal("200.00"))
            session.add(account)
            session.flush()
            session.add(PaperTradeSettings(user_id=user_id, trading_mode="virtual", fee_mode_override="fixed", open_fee_override=Decimal("1.00")))
            session.add(UserNotifySetting(user_id=user_id, notify_trade=False))
            for symbol in ("rb2701", "cu2701"):
                session.add(PaperOrder(account_id=account.id, user_id=user_id, symbol=symbol, direction="buy", offset="open", order_type="limit", price=100, quantity=1, multiplier=10, margin_rate=Decimal("0.1"), frozen_margin=100, created_at=datetime.now(timezone.utc)))
        session.commit()
    yield database, users
    database.dispose()


async def test_matching_refreshes_preloaded_account_after_committed_deposit(matching_database):
    database, users = matching_database
    async with MatchingSession(database) as stale:
        account = (await stale.execute(select(PaperAccount).where(PaperAccount.user_id == users[0]))).scalar_one()
        order = (await stale.execute(select(PaperOrder).where(PaperOrder.account_id == account.id, PaperOrder.symbol == "rb2701"))).scalar_one()
        async with MatchingSession(database) as other:
            await deposit_custom_funds(other, users[0], Decimal("10.25"), uuid4(), "virtual")
            await other.commit()
        assert account.balance == Decimal("100.00")
        assert await matcher._fill_one(stale, order, Decimal("99"), None, None)
        await stale.commit()
    with Session(database) as session:
        assert session.scalar(select(PaperAccount.balance).where(PaperAccount.user_id == users[0])) == Decimal("109.25")
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 2


async def test_matching_rechecks_cancelled_order_after_waiting_for_account(matching_database):
    database, users = matching_database
    async with MatchingSession(database) as stale:
        order = (await stale.execute(select(PaperOrder).where(PaperOrder.user_id == users[0], PaperOrder.symbol == "rb2701"))).scalar_one()
        with Session(database) as other:
            current = other.get(PaperOrder, order.id)
            current.status = "cancelled"
            other.commit()
        assert order.status == "pending"
        assert not await matcher._fill_one(stale, order, Decimal("99"), None, None)
        await stale.commit()
    with Session(database) as session:
        assert session.get(PaperOrder, order.id).status == "cancelled"
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 0
        assert session.scalar(select(func.count()).select_from(PaperPosition)) == 0


async def test_multi_symbol_batch_locks_accounts_in_stable_order(matching_database, monkeypatch):
    database, users = matching_database
    monkeypatch.setattr(matcher, "is_symbol_trading", lambda symbol: True)
    with Session(database) as session:
        for account in session.scalars(select(PaperAccount)):
            account.trading_mode = "live"
        for settings in session.scalars(select(PaperTradeSettings)):
            settings.trading_mode = "live"
        session.commit()
    observed = []

    def factory():
        instance = MatchingSession(database)
        observed.append(instance)
        return instance

    assert await matcher.match_quotes(factory, [{"symbol": "cu2701", "last_price": 99}, {"symbol": "rb2701", "last_price": 99}]) == 4
    locked = observed[0].accounts
    assert set(locked) == set(users)
    assert locked == sorted(locked, key=str)
    with Session(database) as session:
        assert list(session.scalars(select(PaperAccount.balance))) == [Decimal("98.00"), Decimal("98.00")]


async def test_simultaneous_matching_and_deposits_preserve_cash_and_fill_once(matching_database):
    database, users = matching_database
    ready = Barrier(12)

    def fill():
        async def run():
            async with MatchingSession(database) as session:
                order = (await session.execute(select(PaperOrder).where(PaperOrder.user_id == users[0], PaperOrder.symbol == "rb2701"))).scalar_one()
                ready.wait(timeout=10)
                result = await matcher._fill_one(session, order, Decimal("99"), None, None)
                await session.commit()
                return result
        return asyncio.run(run())

    def deposit():
        async def run():
            async with MatchingSession(database) as session:
                ready.wait(timeout=10)
                await deposit_custom_funds(session, users[0], Decimal("10.25"), uuid4(), "virtual")
                await session.commit()
        return asyncio.run(run())

    results = await asyncio.gather(*(asyncio.to_thread(fill) for _ in range(4)), *(asyncio.to_thread(deposit) for _ in range(8)))
    assert sum(result is True for result in results) == 1
    with Session(database) as session:
        assert session.scalar(select(PaperAccount.balance).where(PaperAccount.user_id == users[0])) == Decimal("181.00")
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 9
        assert session.scalar(select(PaperPosition.quantity).where(PaperPosition.user_id == users[0])) == 1
