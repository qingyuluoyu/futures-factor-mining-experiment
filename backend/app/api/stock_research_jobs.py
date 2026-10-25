import asyncio
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import NoMatchFound

from app.core.config import Settings
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.core.market_capability import require_stock_market
from app.models.stock_research_job import StockResearchSchemaVersion
from app.schemas.stock_research_jobs import CreateResearchJob, MAX_ACTIVE_JOBS, MAX_RUNTIME_SECONDS, MAX_USER_ACTIVE_JOBS
from app.services.stock_factors.engine import METHOD_VERSION, ResearchDataError
from app.services.stock_factors.coverage import get_research_coverage
from app.schemas.stock_research import _parse_date
from app.services.stock_research_jobs import JobError, ResearchJobService
from app.api.stock_research_inputs import router as inputs_router


router = APIRouter(prefix="/api/stock/research", tags=["A股持久研究任务与报告"], dependencies=[Depends(require_stock_market)])
logger = logging.getLogger("qihuo.stock_research_jobs")
SCHEMA_VERSION = "20261018_002_stock_research_jobs"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / f"{SCHEMA_VERSION}.sql"


async def research_ready(session: AsyncSession, settings: Settings) -> bool:
    if not settings.stock_research_jobs_enabled:
        return False
    if session.get_bind().dialect.name == "postgresql" and await session.scalar(text("SELECT to_regclass('stock_research_schema_versions')")) is None:
        return False
    checksum = await session.scalar(select(StockResearchSchemaVersion.checksum).where(StockResearchSchemaVersion.version == SCHEMA_VERSION))
    return checksum == hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


async def _coverage_response(request, response, start_date, end_date, ctx, *, factor_ids=None, mktcap_neutral=False, weight="equal"):
    request_id = uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    started = perf_counter()
    try:
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        try:
            start = _parse_date(start_date) if start_date else None
            end = _parse_date(end_date) if end_date else None
        except ValueError as error:
            raise ResearchDataError("invalid_date_range", str(error), 422) from None
        async with asyncio.timeout(12):
            result = await get_research_coverage(request.app.state.session_factory, start, end, today=today, factor_ids=factor_ids, mktcap_neutral=mktcap_neutral, weight=weight)
    except ResearchDataError as error:
        raise HTTPException(error.status, str(error), headers={**headers, "X-Research-Error": error.code}) from None
    except Exception as error:
        logger.error("request_id=%s user_id=%s mode=%s operation=research_coverage error_type=%s", request_id, ctx.user.id, ctx.trading_mode, type(error).__name__)
        raise HTTPException(503, "覆盖预检暂不可用，请重试；未以空数据替代失败", headers=headers) from None
    for name, value in headers.items():
        response.headers[name] = value
    logger.info("request_id=%s user_id=%s mode=%s operation=research_coverage status=%s elapsed_ms=%.1f model_calls=0", request_id, ctx.user.id, ctx.trading_mode, result["status"], (perf_counter() - started) * 1000)
    return result


@router.get("/coverage")
async def research_coverage(request: Request, response: Response, start_date: str | None = Query(None, max_length=10), end_date: str | None = Query(None, max_length=10), ctx: AuthContext = Depends(get_auth_context)) -> dict:
    return await _coverage_response(request, response, start_date, end_date, ctx)


@router.get("/coverage-v2")
async def research_coverage_v2(request: Request, response: Response, factor_ids: list[str] = Query(..., min_length=1, max_length=9),
    start_date: str | None = Query(None, max_length=10), end_date: str | None = Query(None, max_length=10),
    mktcap_neutral: bool = False, weight: Literal["equal", "mktcap", "factor"] = "equal", ctx: AuthContext = Depends(get_auth_context)) -> dict:
    return await _coverage_response(request, response, start_date, end_date, ctx, factor_ids=factor_ids, mktcap_neutral=mktcap_neutral, weight=weight)


async def service_dependency(ctx: AuthContext = Depends(get_auth_context), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> ResearchJobService:
    request_id = uuid4().hex
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(5):
            ready = await research_ready(session, settings)
    except Exception as error:
        logger.error("request_id=%s user_id=%s mode=%s operation=research_readiness error_type=%s", request_id, ctx.user.id, ctx.trading_mode, type(error).__name__)
        raise HTTPException(503, "研究数据库暂不可用，请稍后重试", headers=headers) from None
    if not ready:
        raise HTTPException(503, "股票研究任务未启用或尚未执行匹配的版本迁移", headers=headers)
    return ResearchJobService(session, ctx.user.id, ctx.trading_mode)


async def execute(response: Response, service: ResearchJobService, operation: str, callback, *, commit: bool = False, request_id: str | None = None):
    request_id = request_id or uuid4().hex
    headers = {"X-Request-ID": request_id, "X-Method-Version": METHOD_VERSION, "Cache-Control": "no-store"}
    for name, value in headers.items():
        response.headers[name] = value
    started = perf_counter()
    try:
        async with asyncio.timeout(15):
            result = await callback()
            if commit:
                await service.session.commit()
    except JobError as error:
        await service.session.rollback()
        logger.warning("request_id=%s user_id=%s mode=%s operation=%s error_type=%s elapsed_ms=%.1f", request_id, service.user_id, service.mode, operation, error.code, (perf_counter() - started) * 1000)
        raise HTTPException(error.status, error.message, headers={**headers, "X-Research-Error": error.code, **({"Retry-After": "3"} if error.status == 429 else {})}) from None
    except Exception as error:
        await service.session.rollback()
        logger.error("request_id=%s user_id=%s mode=%s operation=%s error_type=%s", request_id, service.user_id, service.mode, operation, type(error).__name__)
        raise HTTPException(503, "研究任务服务暂不可用；提交失败时请保留原幂等键核对或重试", headers=headers) from None
    logger.info("request_id=%s user_id=%s mode=%s operation=%s elapsed_ms=%.1f model_calls=0", request_id, service.user_id, service.mode, operation, (perf_counter() - started) * 1000)
    return result


@router.get("/availability")
async def availability(request: Request, response: Response, ctx: AuthContext = Depends(get_auth_context), session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    request_id = uuid4().hex
    response.headers["X-Request-ID"] = request_id
    try:
        async with asyncio.timeout(5):
            enabled = await research_ready(session, settings)
    except Exception as error:
        logger.error("request_id=%s user_id=%s mode=%s operation=research_availability error_type=%s", request_id, ctx.user.id, ctx.trading_mode, type(error).__name__)
        raise HTTPException(503, "研究数据库暂不可用，请稍后重试", headers={"X-Request-ID": request_id, "Cache-Control": "no-store"}) from None
    worker = getattr(request.app.state, "stock_research_worker", None)
    accepting = enabled and worker is not None and worker.healthy
    try:
        request.app.url_path_for("download_report", job_id="00000000-0000-0000-0000-000000000000")
        request.app.url_path_for("compare_saved_reports")
        report_capability = enabled
    except NoMatchFound:
        report_capability = False
    try:
        request.app.url_path_for("factor_analysis")
        request.app.url_path_for("stock_backtest")
        legacy_adapter = True
    except NoMatchFound:
        legacy_adapter = False
    return {"enabled": enabled, "accepting": accepting, "saved_report_artifacts": report_capability, "selective_coverage": True, "legacy_sync_enabled": settings.stock_legacy_sync_enabled, "legacy_async_adapter": legacy_adapter, "max_active": MAX_ACTIVE_JOBS, "max_user_active": MAX_USER_ACTIVE_JOBS, "max_runtime_seconds": MAX_RUNTIME_SECONDS, "reason": None if accepting else "研究任务尚未启用或迁移未就绪" if not enabled else "研究执行器暂未就绪，可稍后刷新；已有报告仍可读取"}


@router.post("/jobs", status_code=202)
async def create_job(body: CreateResearchJob, request: Request, response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    worker = getattr(request.app.state, "stock_research_worker", None)
    request_id = uuid4().hex
    return await execute(response, service, "create_research_job", lambda: service.create(body, request_id=request_id, accepting_new=worker is not None and worker.healthy), commit=True, request_id=request_id)


@router.get("/jobs")
async def list_jobs(response: Response, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000), kind: Literal["analysis", "backtest"] | None = None, status: Literal["pending", "running", "completed", "failed", "cancelled", "expired"] | None = None, service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "list_research_jobs", lambda: service.list(limit=limit, offset=offset, kind=kind, status=status))


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "get_research_job", lambda: service.get(job_id))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID, response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "cancel_research_job", lambda: service.cancel(job_id), commit=True)


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: UUID, response: Response, service: ResearchJobService = Depends(service_dependency)) -> dict:
    return await execute(response, service, "get_research_result", lambda: service.result(job_id))


router.include_router(inputs_router)
