"""Offline ASGI verification of private, read-only native model history."""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.core.deps import AuthContext, get_auth_context, get_session
from tests.test_stock_research_job_service import job_database

BASE = "/api/stock/research/v2/models"


def application(factory, owner):
    from app.api.stock_ml_registry import router
    app = FastAPI()
    app.include_router(router, prefix="/api/stock")
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(context.user, context.mode)
    statements = []

    async def sessions():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                statements.extend(session.statements)

    app.dependency_overrides[get_session] = sessions
    return app, context, statements


def assert_private_response(response):
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert len(response.headers["x-request-id"]) == 32


@pytest.mark.asyncio
async def test_disabled_model_history_does_not_probe_schema_store_or_native_engine(tmp_path):
    async with job_database() as (factory, owner, _):
        app, _, statements = application(factory, owner)
        app.state.stock_research_artifact_store = "invalid-config-must-not-be-opened"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(BASE + "/availability")
            assert response.status_code == 200
            assert response.json()["listing_enabled"] is False
            assert response.json()["training_enabled"] is False
            assert_private_response(response)
            for flag in (None, False, "true", 1):
                app.state.stock_ml_registry_enabled = flag
                for suffix in ("", "/" + str(uuid4()), "/" + str(uuid4()) + "/report"):
                    failed = await client.get(BASE + suffix)
                    assert failed.status_code == 503
                    assert failed.json()["detail"]["code"] == "ml_registry_disabled"
                    assert_private_response(failed)
            assert statements == []
            assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_model_history_rejects_market_unauthenticated_and_unbounded_user_overrides():
    async with job_database() as (factory, owner, other):
        app, context, _ = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for query in ("?limit=101", "?limit=0", "?offset=-1", "?offset=100001", "?owner_id=" + str(other), "?trading_mode=live", "?model_path=secret"):
                failed = await client.get(BASE + query)
                assert failed.status_code == 422
                assert failed.json()["detail"]["code"] == "invalid_input"
                assert_private_response(failed)
            assert (await client.get(BASE + "/not-a-uuid")).status_code == 422
            context.user.market_type = "futures"
            denied = await client.get(BASE + "/availability")
            assert denied.status_code == 409 and denied.json()["detail"]["code"] == "stock_required"
            assert_private_response(denied)
        app.dependency_overrides.pop(get_auth_context)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.get(BASE + "/availability")
            assert denied.status_code == 401
            assert_private_response(denied)


@pytest.mark.asyncio
async def test_enabled_without_store_or_schema_fails_closed_not_empty_success(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    async with job_database() as (factory, owner, _):
        app, _, _ = application(factory, owner)
        app.state.stock_ml_registry_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for bad_store in (None, "invalid"):
                app.state.stock_research_artifact_store = bad_store
                status = await client.get(BASE + "/availability")
                assert status.status_code == 200 and status.json()["listing_enabled"] is False
                failed = await client.get(BASE)
                assert failed.status_code == 503 and failed.json()["detail"]["code"] == "artifact_store_unavailable"
            app.state.stock_research_artifact_store = ArtifactStore(tmp_path / "store")
            assert (await client.get(BASE + "/availability")).json()["listing_enabled"] is False
            assert (await client.get(BASE)).status_code == 503


@pytest.mark.asyncio
async def test_real_native_registered_history_list_detail_report_are_private_read_only(tmp_path):
    from app.services.stock_research_v2.model_registry import ModelRegistryService
    from tests.test_stock_ml_registry import prepare_registry, experiment
    from tests.test_stock_research_archive_v2 import service
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "store")
            result = await experiment(archive)
            detail = await ModelRegistryService(archive).register_experiment(
                experiment_sha256=result["saved_report"]["sha256"], idempotency_key=uuid4(), request_id="d"*32)
            await session.commit()
        app, context, statements = application(factory, owner)
        app.state.stock_research_artifact_store = archive.store
        app.state.stock_ml_registry_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            available = await client.get(BASE + "/availability")
            assert available.status_code == 200 and available.json()["listing_enabled"] is True
            assert available.json()["training_enabled"] is False
            listed = await client.get(BASE + "?limit=1")
            assert listed.status_code == 200
            assert listed.json()["total"] == 1 and listed.json()["has_more"] is False
            assert listed.json()["items"][0]["id"] == str(detail.id)
            assert "parameters" not in listed.json()["items"][0]
            assert (await client.get(BASE + "?offset=1")).json() == {"items": [], "total": 1, "has_more": False}
            response = await client.get(BASE + "/" + str(detail.id))
            assert response.status_code == 200 and response.json() == detail.model_dump(mode="json")
            assert_private_response(response)
            report = await client.get(BASE + "/" + str(detail.id) + "/report")
            assert report.status_code == 200
            assert report.json()["kind"] == "stock-walk-forward-calculation-v1"
            assert report.json()["report"]["evaluation"] is None
            assert "model_text" not in report.text
            assert_private_response(report)
            for suffix in ("/availability", "/" + str(detail.id), "/" + str(detail.id) + "/report"):
                assert (await client.get(BASE + suffix + "?owner_id=" + str(other))).status_code == 422
            for identity, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = identity, mode
                assert (await client.get(BASE)).json()["items"] == []
                assert (await client.get(BASE + "/" + str(detail.id))).status_code == 404
                assert (await client.get(BASE + "/" + str(detail.id) + "/report")).status_code == 404
            context.user.id, context.mode = owner, "virtual"
            for suffix in ("", "/" + str(detail.id), "/" + str(detail.id) + "/report"):
                assert (await client.post(BASE + suffix, json={"owner_id": str(other)})).status_code == 405
            assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)


@pytest.mark.asyncio
async def test_real_jwt_controls_user_and_mode_despite_client_headers(tmp_path):
    from sqlalchemy import select
    from app.core.auth import create_access_token
    from app.core.deps import get_settings
    from app.models.user import User
    from app.services.stock_research_v2.model_registry import ModelRegistryService
    from tests.test_stock_ml_registry import prepare_registry, experiment
    from tests.test_stock_research_archive_v2 import service
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        async with factory() as session:
            for identifier in (owner, other):
                user = await session.scalar(select(User).where(User.id == identifier))
                user.market_type = "stock"
            archive = service(session, owner, tmp_path / "store")
            result = await experiment(archive)
            detail = await ModelRegistryService(archive).register_experiment(
                experiment_sha256=result["saved_report"]["sha256"], idempotency_key=uuid4(), request_id="f"*32)
            await session.commit()
        app, _, statements = application(factory, owner)
        app.dependency_overrides.pop(get_auth_context)
        settings = SimpleNamespace(jwt_secret_key="offline-qa-random-not-production-" + uuid4().hex, jwt_algorithm="HS256", jwt_access_token_expire_minutes=5)
        app.dependency_overrides[get_settings] = lambda: settings
        app.state.stock_research_artifact_store = archive.store
        app.state.stock_ml_registry_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for identity, mode, expected in ((owner, "virtual", 200), (other, "virtual", 404), (owner, "live", 404)):
                token = create_access_token(settings, str(identity), "user", trading_mode=mode)
                headers = {"Authorization": "Bearer " + token, "X-Trading-Mode": "virtual", "X-User-ID": str(owner)}
                response = await client.get(BASE + "/" + str(detail.id), headers=headers)
                assert response.status_code == expected
                assert_private_response(response)
            invalid = await client.get(BASE, headers={"Authorization": "Bearer invalid-qa-token"})
            assert invalid.status_code == 401
            async with factory() as session:
                user = await session.scalar(select(User).where(User.id == owner))
                user.status = "frozen"
                await session.commit()
            frozen = await client.get(BASE, headers={"Authorization": "Bearer " + create_access_token(settings, str(owner), "user", "virtual")})
            assert frozen.status_code == 403
            assert_private_response(frozen)
        assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)


@pytest.mark.asyncio
async def test_quarantined_and_corrupt_report_never_return_fallback_or_mutate_history(tmp_path):
    from sqlalchemy import select
    from app.models.stock_ml_registry import StockMLExperiment
    from app.services.stock_research_v2.model_registry import ModelRegistryService
    from tests.test_stock_ml_registry import prepare_registry, experiment
    from tests.test_stock_research_archive_v2 import service
    async with job_database() as (factory, owner, _):
        await prepare_registry(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "store")
            result = await experiment(archive)
            detail = await ModelRegistryService(archive).register_experiment(
                experiment_sha256=result["saved_report"]["sha256"], idempotency_key=uuid4(), request_id="e"*32)
            await session.commit()
        app, _, statements = application(factory, owner)
        app.state.stock_research_artifact_store = archive.store
        app.state.stock_ml_registry_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with factory() as session:
                row = await session.scalar(select(StockMLExperiment).where(StockMLExperiment.id == detail.id))
                original_size = row.report_byte_size
                row.report_byte_size = 8*1024*1024+1
                await session.commit()
            oversized = await client.get(BASE + "/" + str(detail.id) + "/report")
            assert oversized.status_code == 409 and oversized.json()["detail"]["code"] == "model_report_requires_export"
            assert (await client.get(BASE + "/" + str(detail.id))).status_code == 200
            async with factory() as session:
                row = await session.scalar(select(StockMLExperiment).where(StockMLExperiment.id == detail.id))
                row.report_byte_size = original_size
                row.state = "quarantined"
                await session.commit()
            assert (await client.get(BASE)).json()["items"][0]["state"] == "quarantined"
            assert (await client.get(BASE + "/" + str(detail.id))).json()["state"] == "quarantined"
            rejected = await client.get(BASE + "/" + str(detail.id) + "/report")
            assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "ml_registry_quarantined"
            async with factory() as session:
                row = await session.scalar(select(StockMLExperiment).where(StockMLExperiment.id == detail.id))
                row.state = "ready"
                await session.commit()
            (tmp_path / "store" / (detail.report_sha256 + ".json")).write_bytes(b'{"secret_corrupted_qa":true}')
            corrupted = await client.get(BASE + "/" + str(detail.id) + "/report")
            assert corrupted.status_code == 409 and corrupted.json()["detail"]["code"] == "ml_registry_integrity_failed"
            assert "secret_corrupted_qa" not in corrupted.text
            assert_private_response(corrupted)
        assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)


@pytest.mark.asyncio
async def test_model_history_timeout_and_malformed_output_are_sanitized(tmp_path, monkeypatch):
    from app.services.stock_research_v2.model_registry import ModelRegistryService
    from app.services.stock_research_v2.artifacts import ArtifactStore
    async with job_database() as (factory, owner, _):
        app, _, _ = application(factory, owner)
        app.state.stock_ml_registry_enabled = True
        app.state.stock_research_artifact_store = ArtifactStore(tmp_path / "store")

        async def timeout(self, **kwargs):
            raise TimeoutError("secret-upstream-token")

        monkeypatch.setattr(ModelRegistryService, "list", timeout)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(BASE)
            assert response.status_code == 504 and response.json()["detail"]["code"] == "ml_registry_timeout"
            assert "secret-upstream-token" not in response.text
            assert response.headers["x-request-id"] == response.json()["detail"]["request_id"]

            async def malformed(self, **kwargs):
                return {"items": [], "total": -1, "has_more": False}

            monkeypatch.setattr(ModelRegistryService, "list", malformed)
            response = await client.get(BASE)
            assert response.status_code == 503 and "-1" not in response.text
            assert_private_response(response)

            async def crashed(self, **kwargs):
                raise RuntimeError("secret-database-connection-string")

            monkeypatch.setattr(ModelRegistryService, "list", crashed)
            response = await client.get(BASE)
            assert response.status_code == 503 and "secret-database-connection-string" not in response.text
            assert_private_response(response)
