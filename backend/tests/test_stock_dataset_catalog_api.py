from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from app.api.stock_research_v2 import router
from app.core.deps import AuthContext, get_auth_context, get_session
from app.services.stock_research_v2.artifacts import ArtifactStore
from tests.test_stock_research_archive_v2 import prepare, service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_panels_v2 import snapshot

BASE = "/api/stock/research/v2"


@pytest.mark.asyncio
async def test_catalog_deadline_is_a_structured_timeout_not_a_success_or_empty_list(tmp_path, monkeypatch):
    from app.services.stock_research_v2.datasets import DatasetCatalog

    async def exceeded(self, *, limit, offset):
        raise TimeoutError()

    monkeypatch.setattr(DatasetCatalog, "list", exceeded)
    async with job_database() as (factory, owner, other):
        app, context = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(BASE + "/datasets")
            assert response.status_code == 504
            assert response.json()["detail"]["code"] == "dataset_catalog_timeout"
            assert response.headers["X-Request-ID"] == response.json()["detail"]["request_id"]


@pytest.mark.asyncio
async def test_missing_or_replaced_store_root_disables_details_without_recreating_it(tmp_path):
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        root = tmp_path / "artifacts"
        store = ArtifactStore(root)
        app, context = application(factory, owner, store)
        root.rename(tmp_path / "preserved")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            missing = (await client.get(BASE + "/availability")).json()
            assert missing["catalog_enabled"] is True and missing["detail_enabled"] is False
            assert not root.exists()
            root.write_text("qa-not-a-directory", encoding="utf8")
            replaced = (await client.get(BASE + "/availability")).json()
            assert replaced["detail_enabled"] is False
            assert root.read_text(encoding="utf8") == "qa-not-a-directory"


def application(factory, owner, store=None):
    app = FastAPI()
    app.include_router(router, prefix="/api/stock")
    app.state.stock_research_artifact_store = store
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(context.user, context.mode)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    return app, context


@pytest.mark.asyncio
async def test_catalog_is_migration_gated_and_metadata_listing_does_not_require_or_create_a_store(tmp_path):
    async with job_database() as (factory, owner, other):
        app, context = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            unavailable = await client.get(BASE + "/availability")
            assert unavailable.status_code == 200 and unavailable.json()["catalog_enabled"] is False
            assert unavailable.json()["research_enabled"] is False and unavailable.json()["parquet_enabled"] is False
            assert (await client.get(BASE + "/datasets")).status_code == 503
            assert list(tmp_path.iterdir()) == []
            await prepare(factory)
            available = (await client.get(BASE + "/availability")).json()
            assert available["catalog_enabled"] is True and available["detail_enabled"] is False
            assert (await client.get(BASE + "/datasets")).json() == {"items": [], "total": 0, "has_more": False}
            assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_fixed_version_details_are_manifest_only_never_fake_source_health_or_actual_coverage(tmp_path):
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            ref, source = await snapshot(service(session, owner, tmp_path / "artifacts"))
        app, context = application(factory, owner, ArtifactStore(tmp_path / "artifacts"))
        (tmp_path / "artifacts" / source.artifact.relative_path).unlink()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            page = (await client.get(BASE + "/datasets")).json()
            assert page["total"] == 1 and page["items"][0]["integrity"] == "not_checked"
            response = await client.get(f"{BASE}/datasets/{ref.dataset_id}?data_version={ref.data_version}")
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Request-ID"]
            result = response.json()
            assert result["reference"] == ref.model_dump(mode="json")
            assert result["integrity"] == "manifest_verified_sources_not_checked"
            assert result["research_enabled"] is False
            assert result["start"] == "2026-01-05" and result["end"] == "2026-01-06"
            assert result["sources"][0]["row_count"] == 3
            assert "价格" in "".join(result["limitations"])
            assert not any(key in result for key in ("coverage_percent", "qualified", "price"))
            assert (await client.post(BASE + "/datasets/jobs", json={})).status_code == 405
            assert not any("POST" in route.methods for route in router.routes)


@pytest.mark.asyncio
async def test_catalog_ownership_mode_market_schema_and_versions_are_checked_before_file_reads(tmp_path):
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            ref, source = await snapshot(service(session, owner, tmp_path / "artifacts"))
        app, context = application(factory, owner, ArtifactStore(tmp_path / "artifacts"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for identity, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = identity, mode
                assert (await client.get(BASE + "/datasets")).json()["items"] == []
                assert (await client.get(f"{BASE}/datasets/{ref.dataset_id}?data_version={ref.data_version}")).status_code == 404
            context.user.id, context.mode = owner, "virtual"
            wrong = await client.get(f"{BASE}/datasets/{ref.dataset_id}?data_version={'f' * 64}")
            assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "dataset_version_conflict"
            for path in ("/datasets?limit=101", "/datasets?owner_id=" + str(other), f"/datasets/{ref.dataset_id}?data_version=bad"):
                assert (await client.get(BASE + path)).status_code == 422
            context.user.market_type = "futures"
            wrong_market = await client.get(BASE + "/availability")
            assert wrong_market.status_code == 409 and wrong_market.json()["detail"]["code"] == "stock_required"


@pytest.mark.asyncio
async def test_unauthenticated_requests_and_missing_artifact_configuration_fail_safely(tmp_path):
    app = FastAPI()
    app.include_router(router, prefix="/api/stock")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get(BASE + "/availability")
        assert denied.status_code == 401 and denied.json()["detail"]["code"] == "unauthenticated"
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            ref, source = await snapshot(service(session, owner, tmp_path / "artifacts"))
        configured, context = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=configured), base_url="http://test") as client:
            missing = await client.get(f"{BASE}/datasets/{ref.dataset_id}?data_version={ref.data_version}")
            assert missing.status_code == 503 and missing.json()["detail"]["code"] == "artifact_store_unavailable"
