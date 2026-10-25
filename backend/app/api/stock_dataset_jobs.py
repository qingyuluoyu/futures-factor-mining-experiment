import asyncio
import logging
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.deps import AuthContext, get_auth_context, get_session
from app.core.market_capability import require_stock_market
from app.schemas.stock_dataset_jobs_api import CancelDatasetJob, DatasetJobAvailability, DatasetJobDetail, DatasetJobPage, JobListQuery
from app.schemas.stock_research_v2 import Scope
from app.schemas.stock_research_v2_jobs import (CreateBacktestJob, CreateBenchmarkJob, CreateDatasetJob, CreateMLTrainJob,
    CreateReportJob, CreateResearchICJob)
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.jobs import JobService

logger = logging.getLogger("qihuo.stock_dataset_jobs_api")


class DatasetJobRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def traced(request: Request):
            request_id = uuid4().hex
            request.state.stock_v2_job_request_id = request_id
            headers = {"X-Request-ID": request_id, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, {"code": "invalid_input", "message": "任务参数格式或范围无效", "request_id": request_id, "retryable": False}, headers=headers) from None
            except HTTPException as error:
                code = {401: "unauthenticated", 403: "forbidden", 409: "stock_required"}.get(error.status_code, "request_failed")
                detail = error.detail if isinstance(error.detail, dict) else {"code": code, "message": "任务请求未通过访问校验", "retryable": False}
                raise HTTPException(error.status_code, {**detail, "request_id": request_id}, headers={**(error.headers or {}), **headers}) from None
            except Exception as error:
                logger.error("request_id=%s operation=v2_job_response error_type=%s", request_id, type(error).__name__)
                raise HTTPException(503, {"code": "v2_job_response_failed", "message": "任务响应或事务未通过安全验证", "request_id": request_id, "retryable": False}, headers=headers) from None
            response.headers.update(headers)
            return response

        return traced


router = APIRouter(prefix="/research/v2", tags=["A股观察数据任务"], route_class=DatasetJobRoute, dependencies=[Depends(require_stock_market)])


async def service(ctx: AuthContext = Depends(get_auth_context), session=Depends(get_session)):
    return JobService(session, Scope(owner_id=ctx.user.id, trading_mode=ctx.trading_mode))


async def execute(request, queue, operation, callback, schema):
    started = perf_counter()
    request_id = request.state.stock_v2_job_request_id
    try:
        async with asyncio.timeout(8):
            result = schema.model_validate(await callback()).model_dump(mode="json")
    except TimeoutError:
        raise HTTPException(504, {"code": "v2_job_timeout", "message": "任务请求超时，请凭原幂等键查询；未自动重试", "retryable": False}) from None
    except JobError as error:
        raise HTTPException(error.status, {"code": error.code, "message": error.message, "retryable": error.status == 429}) from None
    finally:
        logger.info("request_id=%s user_id=%s mode=%s operation=%s elapsed_ms=%d", request_id, queue.scope.owner_id, queue.scope.trading_mode, operation, (perf_counter() - started) * 1000)
    return result


@router.get("/jobs/availability", response_model=DatasetJobAvailability)
async def availability(request: Request, queue=Depends(service)):
    async def inspect():
        enabled = getattr(request.app.state, "stock_v2_jobs_enabled", False) is True
        accepting = enabled and await queue.available()
        return {"accepting": accepting, "research_enabled": False, "reason": "仅固定观察归档；不能运行因子回测" if accepting else "新任务未开放、未迁移或无有效执行器；既有任务可查询和取消"}
    return await execute(request, queue, "availability", inspect, DatasetJobAvailability)


@router.get("/jobs", response_model=DatasetJobPage)
async def listing(request: Request, query: Annotated[JobListQuery, Query()], queue=Depends(service)):
    return await execute(request, queue, "list", lambda: queue.list(limit=query.limit, offset=query.offset), DatasetJobPage)


@router.get("/jobs/{identifier}", response_model=DatasetJobDetail)
async def detail(identifier: UUID, request: Request, queue=Depends(service)):
    return await execute(request, queue, "get", lambda: queue.get(identifier), DatasetJobDetail)


@router.post("/datasets/jobs", response_model=DatasetJobDetail, status_code=202)
async def submit(command: CreateDatasetJob, request: Request, queue=Depends(service)):
    if getattr(request.app.state, "stock_v2_jobs_enabled", False) is not True:
        raise HTTPException(503, {"code": "v2_jobs_disabled", "message": "观察任务入口未开放；没有新建任务", "retryable": False})
    return await execute(request, queue, "submit", lambda: queue.submit(command, request_id=request.state.stock_v2_job_request_id), DatasetJobDetail)


# One path per research kind, each accepting only its own command schema.  A kind is
# therefore never reachable by posting another kind's body: pydantic refuses the
# foreign payload before the service is called, so no request can rename a job into a
# kernel it was not built for.  They share the observation switch on purpose — that
# switch is the one that starts the v2 executor, and a research kernel without a live
# executor could only ever queue work that nothing runs.
RESEARCH_CLOSED = {"code": "v2_jobs_disabled", "message": "研究任务入口未开放；没有新建任务", "retryable": False}


@router.post("/ic/jobs", response_model=DatasetJobDetail, status_code=202)
async def submit_ic(command: CreateResearchICJob, request: Request, queue=Depends(service)):
    if getattr(request.app.state, "stock_v2_jobs_enabled", False) is not True:
        raise HTTPException(503, RESEARCH_CLOSED)
    return await execute(request, queue, "submit_ic", lambda: queue.submit(command, request_id=request.state.stock_v2_job_request_id), DatasetJobDetail)


@router.post("/backtest/jobs", response_model=DatasetJobDetail, status_code=202)
async def submit_backtest(command: CreateBacktestJob, request: Request, queue=Depends(service)):
    if getattr(request.app.state, "stock_v2_jobs_enabled", False) is not True:
        raise HTTPException(503, RESEARCH_CLOSED)
    return await execute(request, queue, "submit_backtest", lambda: queue.submit(command, request_id=request.state.stock_v2_job_request_id), DatasetJobDetail)


@router.post("/benchmark/jobs", response_model=DatasetJobDetail, status_code=202)
async def submit_benchmark(command: CreateBenchmarkJob, request: Request, queue=Depends(service)):
    if getattr(request.app.state, "stock_v2_jobs_enabled", False) is not True:
        raise HTTPException(503, RESEARCH_CLOSED)
    return await execute(request, queue, "submit_benchmark", lambda: queue.submit(command, request_id=request.state.stock_v2_job_request_id), DatasetJobDetail)


@router.post("/ml/jobs", response_model=DatasetJobDetail, status_code=202)
async def submit_ml(command: CreateMLTrainJob, request: Request, queue=Depends(service)):
    if getattr(request.app.state, "stock_v2_jobs_enabled", False) is not True:
        raise HTTPException(503, RESEARCH_CLOSED)
    return await execute(request, queue, "submit_ml", lambda: queue.submit(command, request_id=request.state.stock_v2_job_request_id), DatasetJobDetail)


@router.post("/report/jobs", response_model=DatasetJobDetail, status_code=202)
async def submit_report(command: CreateReportJob, request: Request, queue=Depends(service)):
    if getattr(request.app.state, "stock_v2_jobs_enabled", False) is not True:
        raise HTTPException(503, RESEARCH_CLOSED)
    return await execute(request, queue, "submit_report", lambda: queue.submit(command, request_id=request.state.stock_v2_job_request_id), DatasetJobDetail)


@router.post("/jobs/{identifier}/cancel", response_model=DatasetJobDetail)
async def cancel(identifier: UUID, request: Request, command: CancelDatasetJob | None = None, queue=Depends(service)):
    return await execute(request, queue, "cancel", lambda: queue.cancel(identifier), DatasetJobDetail)
