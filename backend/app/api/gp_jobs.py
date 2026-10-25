import asyncio
import hashlib
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text

from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.core.market_capability import require_futures_market
from app.schemas.gp_jobs import CreateGpJob, GpJobList, GpJobView, GpReport, MAX_ACTIVE_JOBS, MAX_GENERATION_SECONDS, MAX_JOB_SECONDS, MAX_USER_ACTIVE_JOBS
from app.services.factor_mining.jobs import GpJobError, GpJobService
from scripts.migrate_gp_checkpoint import MIGRATION_PATH as INPUT_PATH, VERSION as INPUT_VERSION
from scripts.migrate_gp_jobs import MIGRATION_PATH as JOB_PATH, VERSION as JOB_VERSION


router = APIRouter(prefix="/api/factor-mining/v2", tags=["持久GP任务v2"], dependencies=[Depends(require_futures_market)])
logger = logging.getLogger("qihuo.gp_api")


async def gp_ready(session, settings):
    if not settings.gp_jobs_enabled:
        return False
    for table, version, path in (("gp_checkpoint_schema_versions", INPUT_VERSION, INPUT_PATH), ("gp_job_schema_versions", JOB_VERSION, JOB_PATH)):
        if await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || :table)"), {"table": "." + table}) is None:
            return False
        checksum = await session.scalar(text(f"SELECT checksum FROM {table} WHERE version=:version"), {"version": version})
        if checksum != hashlib.sha256(path.read_bytes()).hexdigest():
            return False
    return True


async def service_dependency(ctx: AuthContext = Depends(get_auth_context), session=Depends(get_session), settings=Depends(get_settings)):
    headers = {"X-Request-ID": uuid4().hex, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(5):
            ready = await gp_ready(session, settings)
    except Exception as error:
        logger.error("request_id=%s operation=gp_readiness error_type=%s", headers["X-Request-ID"], type(error).__name__)
        raise HTTPException(503, "GP任务存储暂不可用", headers=headers) from None
    if not ready:
        raise HTTPException(503, "GP新任务未启用或008/009匹配迁移未就绪；旧任务没有自动改盘", headers=headers)
    return GpJobService(session, ctx.user.id, ctx.trading_mode)


def accepting(request):
    worker = getattr(request.app.state, "gp_checkpoint_worker", None)
    return worker is not None and worker.healthy


async def execute(response, service, operation, callback, *, commit=False):
    headers = {"X-Request-ID": uuid4().hex, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(15):
            result = await callback()
            if commit:
                await service.session.commit()
    except GpJobError as error:
        await service.session.rollback()
        raise HTTPException(error.status, "GP任务请求被拒绝：" + error.code, headers={**headers, "X-Research-Error": error.code, **({"Retry-After": "3"} if error.status == 429 else {})}) from None
    except Exception as error:
        await service.session.rollback()
        logger.error("request_id=%s user_id=%s mode=%s operation=%s error_type=%s", headers["X-Request-ID"], service.user_id, service.mode, operation, type(error).__name__)
        raise HTTPException(503, "GP服务暂不可用；请保留原幂等键核对，未自动重试", headers=headers) from None
    for name, value in headers.items():
        response.headers[name] = value
    logger.info("request_id=%s user_id=%s mode=%s operation=%s source_calls=0 model_calls=0", headers["X-Request-ID"], service.user_id, service.mode, operation)
    return result


@router.get("/availability")
async def availability(request: Request, response: Response, ctx: AuthContext = Depends(get_auth_context), session=Depends(get_session), settings=Depends(get_settings)):
    response.headers["Cache-Control"], response.headers["X-Request-ID"] = "no-store", uuid4().hex
    try:
        async with asyncio.timeout(5):
            enabled = await gp_ready(session, settings)
    except Exception:
        raise HTTPException(503, "GP任务存储暂不可用", headers={"Cache-Control": "no-store", "X-Request-ID": response.headers["X-Request-ID"]}) from None
    return {"enabled": enabled, "accepting": enabled and accepting(request), "source_ingestion_available": False, "legacy_available": False,
        "max_active": MAX_ACTIVE_JOBS, "max_user_active": MAX_USER_ACTIVE_JOBS, "max_generation_seconds": MAX_GENERATION_SECONDS, "max_job_seconds": MAX_JOB_SECONDS,
        "reason": None if enabled and accepting(request) else "新GP任务未启用、迁移不匹配或执行器未就绪；真实来源冻结入口仍待接通"}


@router.post("/tasks", status_code=202, response_model=GpJobView)
async def create(body: CreateGpJob, request: Request, response: Response, service=Depends(service_dependency)):
    result = await execute(response, service, "gp_job_create", lambda: service.create(body, accepting_new=accepting(request)), commit=True)
    response.headers["Location"] = f"/api/factor-mining/v2/tasks/{result['id']}"
    return result


@router.get("/tasks", response_model=GpJobList)
async def list_jobs(response: Response, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000), service=Depends(service_dependency)):
    return await execute(response, service, "gp_job_list", lambda: service.list(limit=limit, offset=offset))


@router.get("/tasks/{identifier}", response_model=GpJobView)
async def get_job(identifier: UUID, response: Response, service=Depends(service_dependency)):
    return await execute(response, service, "gp_job_get", lambda: service.get(identifier))


@router.post("/tasks/{identifier}/pause", response_model=GpJobView)
async def pause(identifier: UUID, response: Response, service=Depends(service_dependency)):
    return await execute(response, service, "gp_job_pause", lambda: service.pause(identifier), commit=True)


@router.post("/tasks/{identifier}/cancel", response_model=GpJobView)
async def cancel(identifier: UUID, response: Response, service=Depends(service_dependency)):
    return await execute(response, service, "gp_job_cancel", lambda: service.cancel(identifier), commit=True)


@router.post("/tasks/{identifier}/resume", response_model=GpJobView)
async def resume(identifier: UUID, request: Request, response: Response, service=Depends(service_dependency)):
    return await execute(response, service, "gp_job_resume", lambda: service.resume(identifier, accepting_new=accepting(request)), commit=True)


@router.get("/tasks/{identifier}/result", response_model=GpReport)
async def report(identifier: UUID, response: Response, service=Depends(service_dependency)):
    return await execute(response, service, "gp_job_report", lambda: service.result(identifier))
