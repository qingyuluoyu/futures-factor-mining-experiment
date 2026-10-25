import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy import select, func

from app.api.stock_research_reports import router
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.models.stock_research_job import ResearchJobBase, StockResearchJob
from app.services.stock_research_jobs import ResearchJobService, utc_now
from tests.test_stock_research_job_service import job_database, creation


async def application(monkeypatch, factory, owner):
    import app.api.stock_research_jobs as jobs

    async def ready(session, settings):
        return True

    monkeypatch.setattr(jobs, "research_ready", ready)
    app = FastAPI()
    app.include_router(router)
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=context.user, trading_mode=context.mode)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=True)

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    return app, context


async def migrate_fixture(session):
    from app.models.stock_report_bookmark import BookmarkBase, StockReportBookmarkSchemaVersion
    from app.services.stock_report_bookmarks import SCHEMA_PATH, SCHEMA_VERSION
    BookmarkBase.metadata.create_all(session.get_bind())
    session.add(StockReportBookmarkSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
    await session.commit()


async def saved_job(session, owner, mode="virtual", *, completed=True):
    service = ResearchJobService(session, owner, mode)
    view = await service.create(creation(), request_id="a" * 32)
    job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == UUID(view["id"])))
    if completed:
        job.status, job.step, job.progress = "completed", "completed", 100
        job.data_version, job.result_json, job.finished_at = "qa-bookmark-not-price-data", {"qa": True}, utc_now()
    await session.commit()
    return job.id


@pytest.mark.asyncio
async def test_bookmark_capability_is_false_without_its_additive_migration(monkeypatch):
    async with job_database() as (factory, owner, other):
        app, context = await application(monkeypatch, factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/stock/research/bookmarks/availability")
            assert response.status_code == 200
            assert response.json()["enabled"] is False
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_version_marker_without_bookmark_table_does_not_advertise_capability(monkeypatch):
    from app.models.stock_report_bookmark import StockReportBookmarkSchemaVersion
    from app.services.stock_report_bookmarks import SCHEMA_PATH, SCHEMA_VERSION
    async with job_database() as (factory, owner, other):
        async with factory() as session:
            StockReportBookmarkSchemaVersion.__table__.create(session.get_bind())
            session.add(StockReportBookmarkSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
            await session.commit()
        app, context = await application(monkeypatch, factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/stock/research/bookmarks/availability")
            assert response.status_code == 200 and response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_bookmark_desired_state_idempotency_and_original_report_immutable(monkeypatch):
    from app.models.stock_report_bookmark import StockReportBookmark
    async with job_database() as (factory, owner, other):
        async with factory() as session:
            await migrate_fixture(session)
            identifier = await saved_job(session, owner)
            original = await ResearchJobService(session, owner, "virtual").get(identifier)
        app, context = await application(monkeypatch, factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/stock/research/jobs/{identifier}/bookmark"
            assert (await client.get(path)).json()["bookmarked"] is False
            first = await client.put(path, json={"bookmarked": True})
            second = await client.put(path, json={"bookmarked": True})
            assert first.status_code == second.status_code == 200
            assert first.json() == second.json()
            assert first.json()["market"] == "stock" and first.json()["created_at"]
            for body in ({"bookmarked": "false"}, {"bookmarked": 1}, {"bookmarked": True, "user_id": str(other)}):
                assert (await client.put(path, json=body)).status_code == 422
            for user, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = user, mode
                assert (await client.get(path)).status_code == 404
                assert (await client.put(path, json={"bookmarked": False})).status_code == 404
            context.user.id, context.mode = owner, "virtual"
            assert (await client.put(path, json={"bookmarked": False})).json()["created_at"] is None
            assert (await client.put(path, json={"bookmarked": False})).json()["bookmarked"] is False
            context.user.market_type = "futures"
            assert (await client.get(path)).status_code == 409
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(StockReportBookmark)) == 0
            assert await ResearchJobService(session, owner, "virtual").get(identifier) == original
        assert "stock_report_bookmarks" not in ResearchJobBase.metadata.tables


@pytest.mark.asyncio
async def test_only_completed_reports_can_be_bookmarked_and_list_is_scoped_paginated(monkeypatch):
    async with job_database() as (factory, owner, other):
        async with factory() as session:
            await migrate_fixture(session)
            identifiers = [await saved_job(session, owner) for _ in range(23)]
            others = [await saved_job(session, other), await saved_job(session, owner, "live")]
            pending = await saved_job(session, owner, completed=False)
        app, context = await application(monkeypatch, factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for identifier in identifiers:
                assert (await client.put(f"/api/stock/research/jobs/{identifier}/bookmark", json={"bookmarked": True})).status_code == 200
            assert (await client.put(f"/api/stock/research/jobs/{pending}/bookmark", json={"bookmarked": True})).status_code == 409
            pages = []
            for offset in (0, 20):
                response = await client.get("/api/stock/research/bookmarks", params={"limit": 20, "offset": offset, "kind": "analysis", "status": "completed"})
                assert response.status_code == 200
                page = response.json()
                assert page["total"] == 23 and len(page["items"]) == (20 if offset == 0 else 3)
                assert page["has_more"] == (offset == 0)
                pages.extend(item["id"] for item in page["items"])
            assert len(set(pages)) == 23
            assert (await client.get("/api/stock/research/bookmarks", params={"status": "failed"})).json()["total"] == 0
            assert (await client.get("/api/stock/research/bookmarks", params={"limit": 51})).status_code == 422
            assert (await client.get("/api/stock/research/bookmarks", params={"offset": 10001})).status_code == 422
            for user, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = user, mode
                assert (await client.get("/api/stock/research/bookmarks")).json()["items"] == []
