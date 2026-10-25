from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import _register_user, router
from app.core.auth import decode_token, hash_password, verify_password
from app.core.config import Settings
from app.core.deps import get_session, get_settings
from app.models.system_settings import SystemSettings
from app.models.user import User
from app.schemas.auth import RegisterRequest


def registration_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        postgres_user="registration_test",
        postgres_password="registration_test",
        postgres_db="registration_test",
        postgres_host="127.0.0.1",
        postgres_port=5432,
        redis_host="127.0.0.1",
        redis_port=6379,
        jwt_secret_key="registration-test-signing-key-only",
        **overrides,
    )


class RegistrationSession:
    def __init__(self, session: Session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, instance):
        self.session.add(instance)

    async def commit(self):
        self.session.commit()

    async def refresh(self, instance):
        self.session.refresh(instance)

    async def flush(self):
        self.session.flush()

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def registration_app():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    User.__table__.create(engine)
    SystemSettings.__table__.create(engine)
    with Session(engine) as session:
        session.add(SystemSettings(
            id=1,
            registration_enabled=False,
            daily_register_limit=1,
            register_reset_hour=6,
            system_name="Registration test",
        ))
        session.add_all([
            User(username="existing_one", hashed_password=hash_password("secret12")),
            User(username="existing_two", hashed_password=hash_password("secret12")),
            User(
                username="before_window",
                hashed_password=hash_password("secret12"),
                created_at=datetime.now(timezone.utc) - timedelta(days=3),
            ),
        ])
        session.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_settings] = lambda: registration_settings()

        async def session_dependency():
            yield RegistrationSession(session)

        app.dependency_overrides[get_session] = session_dependency
        yield app, session
    engine.dispose()


def test_registration_policy_defaults_open_and_rejects_unknown_values():
    assert registration_settings().registration_policy == "open"
    with pytest.raises(ValidationError):
        registration_settings(registration_policy="anything")


async def test_open_status_ignores_old_closed_settings_and_reports_real_count(registration_app):
    app, _ = registration_app
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/auth/registration-status")
    assert response.status_code == 200
    result = response.json()
    assert result["can_register"] is True
    assert result["registration_policy"] == "open"
    assert result["registration_enabled"] is True
    assert result["daily_register_limit"] == 0
    assert result["remaining"] is None
    assert result["registered_in_window"] == 2
    assert result["configured_registration_enabled"] is False
    assert result["configured_daily_register_limit"] == 1


async def test_open_registration_persists_hashed_password_and_can_login(registration_app):
    app, session = registration_app
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/auth/register", json={
            "username": "new_trader",
            "password": "secret12",
            "phone": "  ",
            "email": "",
            "trading_mode": "virtual",
            "role": "admin",
        })
        assert response.status_code == 201, response.text
        created = session.execute(select(User).where(User.username == "new_trader")).scalar_one()
        assert created.hashed_password != "secret12"
        assert verify_password("secret12", created.hashed_password)
        assert created.role == "user"
        assert created.phone is None
        assert created.email is None
        payload = decode_token(registration_settings(), response.json()["access_token"])
        assert payload["sub"] == str(created.id)
        assert payload["trading_mode"] == "virtual"
        login_response = await client.post("/api/auth/login", json={
            "username": "new_trader", "password": "secret12", "trading_mode": "virtual",
        })
        assert login_response.status_code == 200
        status_response = await client.get("/api/auth/registration-status")
        assert status_response.json()["registered_in_window"] == 3


async def test_registration_duplicate_username_returns_conflict_without_inserting(registration_app):
    app, session = registration_app
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/auth/register", json={
            "username": "existing_one", "password": "secret12",
        })
    assert response.status_code == 409
    assert session.scalar(select(func.count()).select_from(User)) == 3


async def test_registration_exposes_request_trace_without_logging_credentials(registration_app, caplog):
    app, _ = registration_app
    with caplog.at_level("INFO", logger="qihuo.auth"):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/api/auth/register", json={
                "username": "traced_trader", "password": "trace-secret12", "phone": "13800000000",
            })
    assert response.status_code == 201
    request_id = response.headers.get("X-Request-ID")
    assert request_id and len(request_id) == 32
    assert f"request_id={request_id}" in caplog.text
    assert "trace-secret12" not in caplog.text
    assert "13800000000" not in caplog.text


async def test_registration_conflict_keeps_request_trace(registration_app):
    app, _ = registration_app
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/auth/register", json={
            "username": "existing_one", "password": "secret12",
        })
    assert response.status_code == 409
    assert len(response.headers.get("X-Request-ID", "")) == 32


@pytest.mark.parametrize("contact", ["phone", "email"])
async def test_registration_duplicate_contact_returns_conflict(registration_app, contact):
    app, session = registration_app
    payload = {"username": "contact_one", "password": "secret12", contact: (
        "13800000000" if contact == "phone" else "contact@example.com"
    )}
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/auth/register", json=payload)).status_code == 201
        response = await client.post("/api/auth/register", json={**payload, "username": "contact_two"})
    assert response.status_code == 409
    assert session.scalar(select(func.count()).select_from(User)) == 4


async def test_managed_policy_preserves_old_disable_switch(registration_app):
    app, _ = registration_app
    app.dependency_overrides[get_settings] = lambda: registration_settings(registration_policy="managed")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        result = (await client.get("/api/auth/registration-status")).json()
        response = await client.post("/api/auth/register", json={
            "username": "new_trader", "password": "secret12",
        })
    assert result["registration_policy"] == "managed"
    assert result["registration_enabled"] is False
    assert result["daily_register_limit"] == 1
    assert result["can_register"] is False
    assert response.status_code == 403


async def test_managed_policy_preserves_daily_limit(registration_app):
    app, session = registration_app
    session.get(SystemSettings, 1).registration_enabled = True
    session.commit()
    app.dependency_overrides[get_settings] = lambda: registration_settings(registration_policy="managed")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        result = (await client.get("/api/auth/registration-status")).json()
        response = await client.post("/api/auth/register", json={
            "username": "new_trader", "password": "secret12",
        })
    assert result["can_register"] is False
    assert result["remaining"] == 0
    assert result["registered_in_window"] == 2
    assert response.status_code == 403


@pytest.mark.parametrize("field,value", [
    ("username", "ab"),
    ("username", "x" * 51),
    ("username", "bad name"),
    ("password", "short"),
    ("password", "      "),
    ("password", "x" * 73),
    ("password", "密" * 25),
    ("password", "secret\x00"),
    ("phone", "1" * 21),
    ("email", "not-an-email"),
])
async def test_registration_rejects_invalid_input(registration_app, field, value):
    app, _ = registration_app
    payload = {"username": "new_trader", "password": "secret12", field: value}
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/auth/register", json=payload)
    assert response.status_code == 422


async def test_concurrent_unique_conflict_rolls_back_and_returns_409(monkeypatch):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock(side_effect=IntegrityError(
        "INSERT users", {}, SimpleNamespace(sqlstate="23505"),
    ))
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    monkeypatch.setattr("app.api.auth.assert_can_register", AsyncMock())
    with pytest.raises(HTTPException) as captured:
        await _register_user(session, registration_settings(), RegisterRequest(
            username="concurrent_trader", password="secret12",
        ))
    assert captured.value.status_code == 409
    session.rollback.assert_awaited_once()
    session.refresh.assert_not_awaited()


async def test_non_unique_database_failure_is_not_reported_as_conflict(monkeypatch):
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock(side_effect=IntegrityError(
        "INSERT users", {}, SimpleNamespace(sqlstate="23502"),
    ))
    session.rollback = AsyncMock()
    monkeypatch.setattr("app.api.auth.assert_can_register", AsyncMock())
    with pytest.raises(IntegrityError):
        await _register_user(session, registration_settings(), RegisterRequest(
            username="database_failure", password="secret12",
        ))
    session.rollback.assert_awaited_once()
