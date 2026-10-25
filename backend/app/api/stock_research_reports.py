from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.api.stock_research_jobs import execute, service_dependency
from app.core.market_capability import require_stock_market
from app.schemas.stock_report_artifacts import SavedResearchComparison
from app.services.stock_report_artifacts import compare_reports, export_report
from app.services.stock_research_jobs import ResearchJobService
from app.schemas.stock_report_bookmarks import ReportBookmarkView, SetReportBookmark
from app.services.stock_report_bookmarks import bookmarks_ready, get_bookmark, list_bookmarked_reports, set_bookmark


class ReportRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_report_headers(request: Request):
            headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
            try:
                result = await handler(request)
            except HTTPException as error:
                error.headers = {"X-Request-ID": uuid4().hex, **(error.headers or {}), **headers}
                raise
            except RequestValidationError as error:
                result = await request_validation_exception_handler(request, error)
            for name, value in headers.items():
                result.headers[name] = value
            if not result.headers.get("X-Request-ID"):
                result.headers["X-Request-ID"] = uuid4().hex
            return result

        return with_report_headers


router = APIRouter(prefix="/api/stock/research", tags=["A股保存研究导出与对照"], route_class=ReportRoute, dependencies=[Depends(require_stock_market)])


@router.get("/bookmarks/availability")
async def report_bookmark_availability(response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    ready = await execute(response, service, "report_bookmark_availability", lambda: bookmarks_ready(service.session))
    return {"enabled": ready, "reason": None if ready else "报告收藏尚未部署或版本不匹配"}


@router.get("/bookmarks")
async def saved_report_bookmarks(response: Response, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000),
    kind: Literal["analysis", "backtest"] | None = None, status: Literal["pending", "running", "completed", "failed", "cancelled", "expired"] | None = None,
    service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "list_report_bookmarks", lambda: list_bookmarked_reports(service, limit=limit, offset=offset, kind=kind, status=status))


@router.get("/jobs/{job_id}/bookmark", response_model=ReportBookmarkView)
async def saved_report_bookmark(job_id: UUID, response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "get_report_bookmark", lambda: get_bookmark(service, job_id))


@router.put("/jobs/{job_id}/bookmark", response_model=ReportBookmarkView)
async def update_saved_report_bookmark(job_id: UUID, body: SetReportBookmark, response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "set_report_bookmark", lambda: set_bookmark(service, job_id, body.bookmarked), commit=True)


@router.get("/jobs/{job_id}/export")
async def download_report(job_id: UUID, response: Response, format: Literal["json", "csv"] = Query("json"), service: ResearchJobService = Depends(service_dependency)) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    try:
        body, method = await execute(response, service, "export_saved_research", lambda: export_report(service, job_id, format))
    except HTTPException as error:
        error.headers = {**(error.headers or {}), "X-Content-Type-Options": "nosniff"}
        raise
    response.headers["X-Method-Version"] = method
    response.headers["Content-Disposition"] = f'attachment; filename="stock-research-{job_id}.{format}"'
    return Response(content=body, media_type="application/json" if format == "json" else "text/csv; charset=utf-8", headers=dict(response.headers))


@router.get("/compare", response_model=SavedResearchComparison)
async def compare_saved_reports(response: Response, left_id: UUID, right_id: UUID, service: ResearchJobService = Depends(service_dependency)) -> dict:
    response.headers["X-Content-Type-Options"] = "nosniff"
    try:
        result = await execute(response, service, "compare_saved_research", lambda: compare_reports(service, left_id, right_id))
    except HTTPException as error:
        error.headers = {**(error.headers or {}), "X-Content-Type-Options": "nosniff"}
        raise
    response.headers["X-Method-Version"] = result["left"]["job"]["method_version"] or "unrecorded"
    return result
