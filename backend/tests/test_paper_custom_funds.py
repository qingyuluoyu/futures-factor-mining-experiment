from decimal import Decimal
from uuid import uuid4
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.paper import router
from app.core.auth import create_access_token
from app.core.config import Settings
from app.core.deps import get_session, get_settings
from app.models.paper.account import PaperAccount
from app.models.paper.ledger import PaperLedger
from app.models.paper.position import PaperPosition
from app.models.system_settings import SystemSettings
from app.models.user import User


class LocalSession:
    def __init__(self, session):
        self.session = session

    def get_bind(self):
        return self.session.get_bind()

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})

    def add(self, value):
        self.session.add(value)

    async def flush(self):
        self.session.flush()


@pytest.fixture
def paper_app(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'paper-test.sqlite'}")
    for model in (User, SystemSettings, PaperAccount, PaperLedger, PaperPosition):
        model.__table__.create(database)
    settings = Settings.model_construct(
        postgres_user="test", postgres_password="test", postgres_host="127.0.0.1",
        postgres_db="test", postgres_port=5432, redis_host="127.0.0.1", redis_port=6379,
        jwt_secret_key="paper-custom-funds-test-only",
    )
    users = [uuid4(), uuid4(), uuid4()]
    with Session(database) as session:
        session.add_all([User(id=user_id, username=f"paper-{index}", hashed_password="unused", status="frozen" if index == 2 else "active") for index, user_id in enumerate(users)])
        session.add(SystemSettings(id=1, paper_claim_amount=1, paper_total_claim_cap=1))
        session.commit()
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_settings] = lambda: settings

    async def local_session():
        with Session(database) as session:
            try:
                yield LocalSession(session)
                session.commit()
            except Exception:
                session.rollback()
                raise

    application.dependency_overrides[get_session] = local_session

    def headers(index=0, mode="live"):
        return {"Authorization": f"Bearer {create_access_token(settings, str(users[index]), 'user', mode)}"}

    yield application, database, users, headers
    database.dispose()


async def test_custom_deposit_is_idempotent_and_ignores_legacy_caps(paper_app):
    application, database, users, headers = paper_app
    body = {"amount": "2000000.25", "idempotency_key": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url="http://test") as client:
        first = await client.post("/api/paper/account/deposit", json=body, headers=headers())
        assert first.status_code == 200, first.text
        retry = await client.post("/api/paper/account/deposit", json=body, headers=headers())
        assert retry.status_code == 200, retry.text
        assert retry.json()["balance"] == 2000000.25
        assert retry.json()["funding_policy"] == "custom"
        assert retry.json()["custom_deposit_enabled"] is True
        assert retry.json()["total_deposit"] == 2000000.25
        assert retry.json()["total_claimed"] == 0
        conflict = await client.post("/api/paper/account/deposit", json={**body, "amount": "1.00"}, headers=headers())
        assert conflict.status_code == 409
    with Session(database) as session:
        rows = list(session.scalars(select(PaperLedger)))
        assert len(rows) == 1
        assert rows[0].ref_type == "custom_deposit"
        assert rows[0].amount == Decimal("2000000.25")
        assert rows[0].user_id == users[0]


async def test_jwt_user_and_mode_isolate_same_key_and_preserve_frozen_pnl(paper_app):
    application, database, users, headers = paper_app
    with Session(database) as session:
        session.add(PaperAccount(user_id=users[0], trading_mode="live", balance=Decimal("20.00"), frozen_margin=Decimal("30.00"), realized_pnl=Decimal("-7.50"), total_claimed=Decimal("60.00"), last_claim_month="2026-09"))
        session.commit()
    body = {"amount": "10.05", "idempotency_key": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url="http://test") as client:
        live = await client.post("/api/paper/account/deposit", json={**body, "user_id": str(users[1]), "trading_mode": "virtual"}, headers=headers())
        assert live.status_code == 422
        live = await client.post("/api/paper/account/deposit", json=body, headers=headers())
        assert live.status_code == 200, live.text
        assert live.json()["balance"] == 30.05
        assert live.json()["frozen_margin"] == 30
        assert live.json()["total_equity"] == 60.05
        assert live.json()["realized_pnl"] == -7.5
        assert live.json()["total_claimed"] == 60
        assert live.json()["total_deposit"] == 70.05
        assert live.json()["last_claim_month"] == "2026-09"
        for auth in (headers(0, "virtual"), headers(1)):
            result = await client.post("/api/paper/account/deposit", json=body, headers=auth)
            assert result.status_code == 200, result.text
            assert result.json()["balance"] == 10.05
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PaperAccount)) == 3
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 3


@pytest.mark.parametrize("amount", ["0.00", "-1.00", "NaN", "Infinity", "1e3", "1.001", "1", 1, True, "10000000000000000.00", " 1.00"])
async def test_invalid_deposit_has_no_financial_side_effect(paper_app, amount):
    application, database, _, headers = paper_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url="http://test") as client:
        result = await client.post("/api/paper/account/deposit", json={"amount": amount, "idempotency_key": str(uuid4())}, headers=headers())
        assert result.status_code == 422, result.text
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 0
        assert session.scalar(select(func.count()).select_from(PaperAccount)) == 0


async def test_auth_and_balance_overflow_reject_without_ledger(paper_app):
    application, database, users, headers = paper_app
    body = {"amount": "0.01", "idempotency_key": str(uuid4())}
    with Session(database) as session:
        session.add(PaperAccount(user_id=users[0], trading_mode="live", balance=Decimal("9999999999999999.99")))
        session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application), base_url="http://test") as client:
        assert (await client.post("/api/paper/account/deposit", json=body)).status_code == 401
        assert (await client.post("/api/paper/account/deposit", json=body, headers=headers(2))).status_code == 403
        assert (await client.post("/api/paper/account/deposit", json=body, headers=headers())).status_code == 409
        assert (await client.post("/api/paper/account/deposit", json={**body, "idempotency_key": "bad"}, headers=headers())).status_code == 422
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 0


async def test_concurrent_transactions_apply_once_and_do_not_lose_other_deposits(paper_app):
    from app.services.paper.account_svc import deposit_custom_funds

    _, database, users, _ = paper_app
    shared_key = uuid4()

    def deposit(key):
        with Session(database) as session:
            result = asyncio.run(deposit_custom_funds(LocalSession(session), users[0], Decimal("10.25"), key))
            session.commit()
            return result

    same = await asyncio.gather(*(asyncio.to_thread(deposit, shared_key) for _ in range(8)))
    assert sum(not result["replayed"] for result in same) == 1
    await asyncio.gather(*(asyncio.to_thread(deposit, uuid4()) for _ in range(12)))
    with Session(database) as session:
        assert session.scalar(select(PaperAccount.balance)) == Decimal("133.25")
        assert session.scalar(select(func.count()).select_from(PaperLedger)) == 13


async def test_ledger_and_balance_roll_back_together_before_retry(paper_app, monkeypatch):
    from app.services.paper import account_svc

    application, database, _, headers = paper_app
    body = {"amount": "10.05", "idempotency_key": str(uuid4())}
    original = account_svc.get_account_summary

    async def fail_summary(*args):
        raise RuntimeError("database read failed before commit")

    monkeypatch.setattr(account_svc, "get_account_summary", fail_summary)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(application, raise_app_exceptions=False), base_url="http://test") as client:
        failed = await client.post("/api/paper/account/deposit", json=body, headers=headers())
        assert failed.status_code == 500
        with Session(database) as session:
            assert session.scalar(select(func.count()).select_from(PaperLedger)) == 0
            assert session.scalar(select(func.count()).select_from(PaperAccount)) == 0
        monkeypatch.setattr(account_svc, "get_account_summary", original)
        retry = await client.post("/api/paper/account/deposit", json=body, headers=headers())
        assert retry.status_code == 200, retry.text
        assert retry.json()["balance"] == 10.05
