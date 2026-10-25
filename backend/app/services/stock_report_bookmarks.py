import hashlib
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, func, inspect, select, text, true
from sqlalchemy.orm import defer

from app.models.stock_report_bookmark import StockReportBookmark, StockReportBookmarkSchemaVersion
from app.models.stock_research_job import StockResearchJob
from app.services.stock_research_jobs import JobError, ResearchJobService, iso_datetime, job_view

SCHEMA_VERSION = "20261022_014_stock_report_bookmarks"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / f"{SCHEMA_VERSION}.sql"


async def bookmarks_ready(session) -> bool:
    if session.get_bind().dialect.name == "postgresql":
        exists = await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.stock_report_bookmark_schema_versions') IS NOT NULL AND to_regclass(quote_ident(current_schema()) || '.stock_report_bookmarks') IS NOT NULL"))
    else:
        inspector = inspect(session.get_bind())
        exists = inspector.has_table("stock_report_bookmark_schema_versions") and inspector.has_table("stock_report_bookmarks")
    if not exists:
        return False
    checksum = await session.scalar(select(StockReportBookmarkSchemaVersion.checksum).where(StockReportBookmarkSchemaVersion.version == SCHEMA_VERSION))
    return checksum == hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


async def require_bookmarks(service: ResearchJobService):
    if not await bookmarks_ready(service.session):
        raise JobError("bookmark_unavailable", "报告收藏尚未部署或版本不匹配", 503)


def bookmark_view(identifier: UUID, row: StockReportBookmark | None) -> dict:
    return {"job_id": str(identifier), "market": "stock", "bookmarked": row is not None, "created_at": iso_datetime(row.created_at) if row is not None else None}


async def get_bookmark(service: ResearchJobService, identifier: UUID) -> dict:
    await require_bookmarks(service)
    await service.row(identifier)
    row = await service.session.scalar(select(StockReportBookmark).where(StockReportBookmark.job_id == identifier))
    return bookmark_view(identifier, row)


async def set_bookmark(service: ResearchJobService, identifier: UUID, desired: bool) -> dict:
    await require_bookmarks(service)
    if service.session.get_bind().dialect.name == "postgresql":
        await service.session.execute(text("SET LOCAL lock_timeout='3s'"))
    job = await service.row(identifier, lock=True)
    if desired and (job.status != "completed" or not job.data_version or job.finished_at is None):
        raise JobError("report_not_completed", "仅可收藏已完成且记录数据版本的研究报告", 409)
    row = await service.session.scalar(select(StockReportBookmark).where(StockReportBookmark.job_id == identifier))
    if desired and row is None:
        row = StockReportBookmark(job_id=identifier)
        service.session.add(row)
        await service.session.flush()
    elif not desired:
        await service.session.execute(delete(StockReportBookmark).where(StockReportBookmark.job_id == identifier))
        row = None
    return bookmark_view(identifier, row)


async def list_bookmarked_reports(service: ResearchJobService, *, limit: int, offset: int, kind: str | None, status: str | None) -> dict:
    await require_bookmarks(service)
    query = select(StockResearchJob.id, StockReportBookmark.created_at).join(StockReportBookmark, StockReportBookmark.job_id == StockResearchJob.id).where(
        StockResearchJob.user_id == service.user_id, StockResearchJob.trading_mode == service.mode, StockResearchJob.status == "completed")
    if kind is not None:
        query = query.where(StockResearchJob.kind == kind)
    if status is not None:
        query = query.where(StockResearchJob.status == status)
    scoped = query.cte("owned_report_bookmarks")
    counted = select(func.count().label("total")).select_from(scoped).cte("bookmark_count")
    page = select(scoped).order_by(scoped.c.created_at.desc(), scoped.c.id.desc()).offset(offset).limit(limit).cte("bookmark_page")
    statement = select(StockResearchJob, counted.c.total).select_from(counted).outerjoin(page, true()).outerjoin(StockResearchJob, StockResearchJob.id == page.c.id).order_by(
        page.c.created_at.desc(), page.c.id.desc()).options(defer(StockResearchJob.result_json), defer(StockResearchJob.params_json), defer(StockResearchJob.data_manifest))
    rows = (await service.session.execute(statement)).all()
    items = [job_view(job) for job, total in rows if job is not None]
    total = rows[0][1]
    return {"items": items, "total": total, "has_more": offset + len(items) < total}
