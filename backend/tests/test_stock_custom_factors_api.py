import hashlib
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import select

from app.api.stock_factors import router
from app.core.deps import AuthContext, get_auth_context, get_session
from tests.test_stock_research_job_service import job_database

BASE = "/api/stock/research/v2/custom-factors"


async def application(factory, owner):
    app = FastAPI()
    app.include_router(router)
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(context.user, context.mode)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    return app, context


async def prepare(factory):
    from app.models.stock_custom_factor import CustomFactorBase, CustomFactorSchemaVersion
    from app.services.stock_research_v2.custom_factors import SCHEMA_PATH, SCHEMA_VERSION
    async with factory() as session:
        CustomFactorBase.metadata.create_all(session.get_bind())
        session.add(CustomFactorSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()


def command(**changes):
    return {"idempotency_key": str(uuid4()), "name": "20日动量", "expression": "close / lag(close, 20) - 1", "direction": "high", "description": "只用过去数据", **changes}


@pytest.mark.asyncio
async def test_readiness_gates_writes_but_validation_is_offline_and_market_scoped():
    async with job_database() as (factory, owner, other):
        app, context = await application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            available = await client.get(BASE + "/availability")
            assert available.status_code == 200
            assert available.json()["definitions_enabled"] is False
            assert available.json()["research_enabled"] is False
            assert available.headers["Cache-Control"] == "no-store"
            valid = await client.post(BASE + "/validate", json={"expression": "close/lag(close,20)-1"})
            assert valid.status_code == 200 and valid.json()["lookback"] == 20
            assert valid.json()["fields"] == ["close"]
            unavailable = await client.post(BASE, json=command())
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "custom_factors_unavailable"
            for expression in ['__import__("os")', "lag(close,-1)", "close.__class__"]:
                invalid = await client.post(BASE + "/validate", json={"expression": expression})
                assert invalid.status_code == 422
                assert invalid.json()["detail"]["request_id"] == invalid.headers["X-Request-ID"]
            context.user.market_type = "futures"
            wrong_market = await client.post(BASE + "/validate", json={"expression": "close"})
            assert wrong_market.status_code == 409
            assert wrong_market.json()["detail"]["code"] == "stock_required"


@pytest.mark.asyncio
async def test_save_edit_disable_fixed_versions_and_idempotent_receipts():
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        app, context = await application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = command()
            response = await client.post(BASE, json=body)
            assert response.status_code == 200
            first = response.json()
            identifier = first["id"]
            assert first["revision"] == 1 and first["disabled"] is False
            assert first["lookback"] == 20 and first["formula_version"] == "stock-expression-v1"
            assert len(first["expression_version"]) == 64
            assert (await client.post(BASE, json=body)).json() == first
            conflict = await client.post(BASE, json={**body, "name": "同键不同参数"})
            assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "idempotency_conflict"
            revised = command(name="十日动量", expression="close/lag(close,10)-1", expected_revision=1)
            second_response = await client.put(f"{BASE}/{identifier}", json=revised)
            assert second_response.status_code == 200
            second = second_response.json()
            assert second["revision"] == 2 and second["lookback"] == 10
            assert (await client.put(f"{BASE}/{identifier}", json=revised)).json() == second
            stale = await client.put(f"{BASE}/{identifier}", json={**revised, "idempotency_key": str(uuid4())})
            assert stale.status_code == 409
            old = await client.get(f"{BASE}/{identifier}/versions/1")
            assert old.json()["expression_version"] == first["expression_version"]
            assert old.json()["name"] == first["name"]
            history = (await client.get(f"{BASE}/{identifier}/versions?limit=1&offset=1")).json()
            assert history["total"] == 2 and history["items"][0]["revision"] == 1
            disabled = await client.post(f"{BASE}/{identifier}/disable", json={"expected_revision": 2, "idempotency_key": str(uuid4())})
            assert disabled.status_code == 200 and disabled.json()["disabled"] is True
            assert (await client.get(f"{BASE}/{identifier}/versions/1")).json()["disabled"] is True
            assert (await client.post(BASE, json=body)).json() == first
            assert (await client.put(f"{BASE}/{identifier}", json=command(expected_revision=2))).status_code == 409
            assert (await client.get(BASE + "?limit=1&offset=50")).json() == {"items": [], "total": 1, "has_more": False}


@pytest.mark.asyncio
async def test_owner_mode_permissions_strict_schema_and_evaluation_of_fixed_ref():
    import pandas as pd
    from app.services.stock_research_v2.custom_factors import CustomFactorService

    async with job_database() as (factory, owner, other):
        await prepare(factory)
        app, context = await application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            original = (await client.post(BASE, json=command(expression="close/lag(close,1)-1"))).json()
            identifier = original["id"]
            for user_id, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = user_id, mode
                assert (await client.get(BASE)).json()["items"] == []
                assert (await client.get(f"{BASE}/{identifier}/versions/1")).status_code == 404
                assert (await client.post(f"{BASE}/{identifier}/disable", json={"expected_revision": 1, "idempotency_key": str(uuid4())})).status_code == 404
            context.user.id, context.mode = owner, "virtual"
            for change in ({"name": " "}, {"direction": "random"}, {"unexpected": True}, {"expression": "close * " + "9" * 800}, {"idempotency_key": "bad"}):
                assert (await client.post(BASE, json=command(**change))).status_code == 422
            assert (await client.get(BASE + "?limit=101")).status_code == 422
        async with factory() as session:
            reference = {"id": UUID(identifier), "revision": 1, "expression_version": original["expression_version"]}
            compiled = await CustomFactorService(session, owner, "virtual").resolve(**reference)
            from app.services.stock_research_v2.expressions import evaluate_expression
            close = pd.DataFrame({"000001.SZ": [10.0, 11.0, 9.9]}, index=pd.date_range("2026-01-01", periods=3))
            result = evaluate_expression(compiled, {"close": close})
            assert result.iloc[1, 0] == pytest.approx(0.1)
            assert result.iloc[2, 0] == pytest.approx(-0.1)
            from app.services.stock_research_jobs import JobError
            with pytest.raises(JobError) as mismatch:
                await CustomFactorService(session, owner, "virtual").resolve(**{**reference, "expression_version": "f" * 64})
            assert mismatch.value.code == "factor_version_conflict" and mismatch.value.status == 409


@pytest.mark.asyncio
async def test_bad_checksum_and_missing_table_cannot_advertise_readiness():
    from app.models.stock_custom_factor import CustomFactorSchemaVersion, StockCustomFactorVersion
    from app.services.stock_research_v2.custom_factors import SCHEMA_PATH
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        app, context = await application(factory, owner)
        async with factory() as session:
            marker = await session.scalar(select(CustomFactorSchemaVersion))
            marker.checksum = "f" * 64
            await session.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(BASE + "/availability")).json()["definitions_enabled"] is False
        async with factory() as session:
            marker = await session.scalar(select(CustomFactorSchemaVersion))
            marker.checksum = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
            await session.commit()
            StockCustomFactorVersion.__table__.drop(session.get_bind())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(BASE + "/availability")).json()["definitions_enabled"] is False


@pytest.mark.asyncio
async def test_no_authentication_and_invalid_expression_never_persist_partial_heads():
    from app.models.stock_custom_factor import StockCustomFactor, StockCustomFactorMutation, StockCustomFactorVersion
    from sqlalchemy import func
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        unauthenticated = FastAPI()
        unauthenticated.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=unauthenticated), base_url="http://test") as client:
            denied = await client.get(BASE)
            assert denied.status_code == 401
            assert denied.json()["detail"]["code"] == "unauthenticated"
        app, context = await application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post(BASE, json=command(expression="close.__class__"))).status_code == 422
        async with factory() as session:
            for model in (StockCustomFactor, StockCustomFactorVersion, StockCustomFactorMutation):
                assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
async def test_disabled_refs_fail_new_selection_but_preserve_explicit_historical_resolution():
    from app.schemas.stock_custom_factor import CustomFactorCreate, CustomFactorDisable
    from app.services.stock_research_v2.custom_factors import CustomFactorService
    from app.services.stock_research_jobs import JobError
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            service = CustomFactorService(session, owner, "virtual")
            record = await service.mutate(CustomFactorCreate.model_validate(command()), request_id="a" * 32)
            await session.commit()
            reference = {"id": UUID(record["id"]), "revision": 1, "expression_version": record["expression_version"]}
            await service.mutate(CustomFactorDisable(idempotency_key=uuid4(), expected_revision=1), identifier=reference["id"], disable=True, request_id="b" * 32)
            await session.commit()
            with pytest.raises(JobError) as blocked:
                await service.resolve(**reference)
            assert blocked.value.code == "factor_disabled"
            assert (await service.resolve(**reference, allow_disabled=True)).version == record["expression_version"]
