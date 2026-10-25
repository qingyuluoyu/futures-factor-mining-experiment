"""Verify real PostgreSQL migration and HTTP in a disposable private schema, never public writes."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from migrate_notification_popup import assert_local_settings, migrate_notification_popup
from migrate_stock_replay import create_replay_engine, load_runtime_settings
from app.api.users import router
from app.core.deps import get_current_user, get_session
from app.models.user import User
from app.models.user_notify_setting import UserNotifySetting
from app.services.notify.notify_svc import is_category_enabled


async def run():
    schema = f"notification_popup_verify_{uuid4().hex}"
    assert schema.startswith("notification_popup_verify_") and schema.replace("_", "").isalnum()
    settings = load_runtime_settings()
    assert_local_settings(settings)
    base_engine = create_replay_engine(settings)
    private_engine = create_async_engine(
        base_engine.url, echo=False,
        connect_args={"timeout": 10, "command_timeout": 30, "server_settings": {"search_path": schema}},
    )
    checks = []
    try:
        async with base_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with private_engine.begin() as connection:
            assert (await connection.execute(text("SELECT current_schema()"))).scalar_one() == schema
            await connection.run_sync(User.__table__.create)
            await connection.execute(text(
                "CREATE TABLE user_notify_settings (id UUID PRIMARY KEY, user_id UUID NOT NULL UNIQUE REFERENCES users(id), "
                "notify_trade BOOLEAN NOT NULL DEFAULT TRUE, notify_fund BOOLEAN NOT NULL DEFAULT TRUE, "
                "notify_price_alert BOOLEAN NOT NULL DEFAULT TRUE, notify_sound_enabled BOOLEAN NOT NULL DEFAULT TRUE)"
            ))
        factory = async_sessionmaker(private_engine, expire_on_commit=False)
        user_a, user_b = uuid4(), uuid4()
        async with factory() as session:
            session.add_all([User(id=user_a, username="verify_a", hashed_password="unused-private-fixture"), User(id=user_b, username="verify_b", hashed_password="unused-private-fixture")])
            await session.flush()
            await session.execute(text("INSERT INTO user_notify_settings(id,user_id) VALUES (:id,:user_id)"), {"id": uuid4(), "user_id": user_a})
            await session.commit()
        try:
            await migrate_notification_popup(private_engine, check=True)
            raise AssertionError("pre-migration check must fail")
        except RuntimeError as error:
            assert "not applied" in str(error)
        checks.append("check-before-apply-does-not-write")
        assert (await migrate_notification_popup(private_engine, check=False))["applied"] is True
        assert (await migrate_notification_popup(private_engine, check=False))["applied"] is False
        assert (await migrate_notification_popup(private_engine, check=True))["applied"] is False
        checks.append("additive-migration-repeat-and-readonly-check")

        app = FastAPI()
        app.include_router(router)

        async def current_user(x_fixture_user: str | None = Header(default=None)):
            if x_fixture_user not in (str(user_a), str(user_b)):
                raise HTTPException(status_code=401, detail="fixture authentication required")
            async with factory() as session:
                return await session.get(User, x_fixture_user)

        async def private_session():
            async with factory() as session:
                try:
                    yield session
                finally:
                    await session.rollback()

        app.dependency_overrides[get_current_user] = current_user
        app.dependency_overrides[get_session] = private_session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://private-test") as client:
            endpoint = "/api/users/me/notify-settings"
            assert (await client.get(endpoint)).status_code == 401
            headers_a, headers_b = {"X-Fixture-User": str(user_a)}, {"X-Fixture-User": str(user_b)}
            for headers in (headers_a, headers_b):
                response = await client.get(endpoint, headers=headers)
                assert response.status_code == 200 and response.json()["notify_popup_enabled"] is True
                assert response.headers["cache-control"] == "no-store" and response.headers["x-request-id"]
            checks.append("old-row-new-row-defaults-and-authentication")
            for value in ("false", "true", 0, 1, None):
                response = await client.put(endpoint, headers=headers_a, json={"notify_popup_enabled": value})
                assert response.status_code == 422
            checks.append("http-strict-boolean")
            for value in (False, False, True, False):
                response = await client.put(endpoint, headers=headers_a, json={"notify_popup_enabled": value, "user_id": str(user_b)})
                assert response.status_code == 200 and response.json()["notify_popup_enabled"] is value
                assert (await client.get(endpoint, headers=headers_a)).json()["notify_popup_enabled"] is value
                assert (await client.get(endpoint, headers=headers_b)).json()["notify_popup_enabled"] is True
            checks.append("persist-readback-idempotence-and-owner-isolation")
        async with factory() as session:
            row = (await session.execute(select(UserNotifySetting).where(UserNotifySetting.user_id == user_a))).scalar_one()
            assert row.notify_popup_enabled is False and row.notify_sound_enabled is True
            for category in ("system", "risk", "trade", "fund"):
                assert is_category_enabled(row, category)
            assert (await session.execute(text("SELECT COUNT(*) FROM user_notify_settings"))).scalar_one() == 2
        checks.append("generation-and-sound-flags-preserved")
        user_c = uuid4()
        async with factory() as session:
            session.add(User(id=user_c, username="verify_concurrent", hashed_password="unused-private-fixture"))
            await session.commit()

        async def first_read():
            from app.services.notify.preferences import get_or_create_preferences
            async with factory() as session:
                row = await get_or_create_preferences(session, user_c)
                await session.commit()
                return row.id

        first, second = await asyncio.gather(first_read(), first_read())
        assert first == second
        checks.append("concurrent-first-read-single-row")
    finally:
        await private_engine.dispose()
        async with base_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await base_engine.dispose()
    print(json.dumps({"status": "passed", "checks": checks, "public_writes": 0, "private_schema_removed": True}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run())
