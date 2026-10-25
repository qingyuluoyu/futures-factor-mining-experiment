"""Opt-in, private read-only native model history. No evaluation or task launch."""
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
from app.schemas.stock_ml_registry import ModelDetail, ModelPage, ModelReport
from app.schemas.stock_ml_registry_api import ModelListQuery, ModelReadQuery, ModelRegistryAvailability
from app.schemas.stock_research_v2 import Scope
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import ArchiveService
from app.services.stock_research_v2.artifacts import ArtifactStore

logger = logging.getLogger("qihuo.stock_ml_registry_api")


class ModelRegistryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def traced(request: Request):
            request_id = uuid4().hex
            request.state.stock_ml_registry_request_id = request_id
            headers = {"X-Request-ID": request_id, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, {"code": "invalid_input", "message": "模型历史请求参数格式或范围无效", "request_id": request_id, "retryable": False}, headers=headers) from None
            except HTTPException as error:
                code = {401: "unauthenticated", 403: "forbidden", 409: "stock_required"}.get(error.status_code, "request_failed")
                detail = error.detail if isinstance(error.detail, dict) else {"code": code, "message": "模型历史请求未通过访问校验", "retryable": False}
                raise HTTPException(error.status_code, {**detail, "request_id": request_id}, headers={**(error.headers or {}), **headers}) from None
            except Exception as error:
                logger.error("request_id=%s operation=ml_registry_response error_type=%s", request_id, type(error).__name__)
                raise HTTPException(503, {"code": "ml_registry_response_failed", "message": "模型历史响应未通过安全验证", "request_id": request_id, "retryable": False}, headers=headers) from None
            response.headers.update(headers)
            return response

        return traced


router = APIRouter(prefix="/research/v2/models", tags=["A股私有模型历史"], route_class=ModelRegistryRoute, dependencies=[Depends(require_stock_market)])


async def service(request: Request, ctx: AuthContext = Depends(get_auth_context), session=Depends(get_session)):
    # An unconfigured/default-closed API must not inspect the registry or load native ML.
    if getattr(request.app.state, "stock_ml_registry_enabled", False) is not True:
        return None
    store = getattr(request.app.state, "stock_research_artifact_store", None)
    if not isinstance(store, ArtifactStore):
        return None
    from app.services.stock_research_v2.model_registry import ModelRegistryService
    return ModelRegistryService(ArchiveService(session, Scope(owner_id=ctx.user.id, trading_mode=ctx.trading_mode), store))


def require_registry(request, registry):
    if getattr(request.app.state, "stock_ml_registry_enabled", False) is not True:
        raise HTTPException(503, {"code": "ml_registry_disabled", "message": "模型历史入口未开放", "retryable": False})
    if registry is None:
        raise HTTPException(503, {"code": "artifact_store_unavailable", "message": "固定产物存储未配置或类型无效；没有创建目录", "retryable": False})


async def execute(request, registry, operation, callback, schema):
    request_id = request.state.stock_ml_registry_request_id
    started = perf_counter()
    try:
        async with asyncio.timeout(8):
            return schema.model_validate(await callback()).model_dump(mode="json")
    except TimeoutError:
        raise HTTPException(504, {"code": "ml_registry_timeout", "message": "模型历史读取超时；未返回空结果或自动重试", "retryable": False}) from None
    except JobError as error:
        raise HTTPException(error.status, {"code": error.code, "message": error.message, "retryable": False}) from None
    finally:
        scope = registry.archive.scope
        logger.info("request_id=%s user_id=%s mode=%s operation=%s elapsed_ms=%.1f writes=0 model_calls=0", request_id, scope.owner_id, scope.trading_mode, operation, (perf_counter() - started) * 1000)


@router.get("/availability", response_model=ModelRegistryAvailability)
async def availability(request: Request, query: Annotated[ModelReadQuery, Query()], registry=Depends(service)):
    if registry is None:
        return ModelRegistryAvailability(listing_enabled=False, reason="模型历史未开放或产物存储未配置；训练入口未开放")

    async def inspect():
        ready = await registry.available()
        return ModelRegistryAvailability(listing_enabled=ready, reason="仅可读取本人当前盘已登记研究模型；训练入口未开放" if ready else "模型登记未迁移或校验不一致；训练入口未开放")
    return await execute(request, registry, "availability", inspect, ModelRegistryAvailability)


@router.get("", response_model=ModelPage)
async def listing(request: Request, query: Annotated[ModelListQuery, Query()], registry=Depends(service)):
    require_registry(request, registry)
    return await execute(request, registry, "list", lambda: registry.list(limit=query.limit, offset=query.offset), ModelPage)


@router.get("/{identifier}", response_model=ModelDetail)
async def detail(identifier: UUID, request: Request, query: Annotated[ModelReadQuery, Query()], registry=Depends(service)):
    require_registry(request, registry)
    return await execute(request, registry, "get", lambda: registry.get(identifier), ModelDetail)


@router.get("/{identifier}/report", response_model=ModelReport)
async def report(identifier: UUID, request: Request, query: Annotated[ModelReadQuery, Query()], registry=Depends(service)):
    require_registry(request, registry)
    return await execute(request, registry, "read_report", lambda: registry.read_report(identifier), ModelReport)
