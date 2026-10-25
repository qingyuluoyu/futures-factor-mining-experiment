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
from app.schemas.stock_dataset_inspection import DatasetAvailability, DatasetCatalogPage, DatasetDescription, DatasetListQuery, DatasetLookup
from app.schemas.stock_research_v2 import DatasetRef, Scope
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import ArchiveService
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.datasets import DatasetCatalog

logger = logging.getLogger("qihuo.stock_dataset_catalog")


class DatasetRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def traced(request: Request):
            request_id = uuid4().hex
            request.state.stock_dataset_request_id = request_id
            headers = {"X-Request-ID": request_id, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, {"code": "invalid_input", "message": "固定数据集请求参数或版本格式无效", "request_id": request_id, "retryable": False}, headers=headers) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **headers}
                if not isinstance(error.detail, dict):
                    error.detail = {"code": {401: "unauthenticated", 403: "forbidden", 409: "stock_required"}.get(error.status_code, "request_failed"), "message": str(error.detail)[:500], "retryable": False}
                error.detail["request_id"] = request_id
                raise
            except Exception as error:
                logger.error("request_id=%s operation=catalog_response error_type=%s", request_id, type(error).__name__)
                raise HTTPException(503, {"code": "catalog_response_failed", "message": "固定数据响应未通过安全验证", "request_id": request_id, "retryable": True}, headers=headers) from None
            response.headers.update(headers)
            return response

        return traced


router = APIRouter(prefix="/research/v2", tags=["A股固定数据只读目录"], route_class=DatasetRoute, dependencies=[Depends(require_stock_market)])


async def service(request: Request, ctx: AuthContext = Depends(get_auth_context), session=Depends(get_session)):
    store = getattr(request.app.state, "stock_research_artifact_store", None)
    if store is not None and not isinstance(store, ArtifactStore):
        raise HTTPException(503, {"code": "artifact_store_unavailable", "message": "固定产物配置类型无效", "retryable": False})
    return DatasetCatalog(ArchiveService(session, Scope(owner_id=ctx.user.id, trading_mode=ctx.trading_mode), store))


async def execute(request, catalog, operation, callback):
    request_id = request.state.stock_dataset_request_id
    started = perf_counter()
    try:
        async with asyncio.timeout(8):
            result = await callback()
    except TimeoutError:
        logger.warning("request_id=%s user_id=%s mode=%s operation=%s error_type=timeout", request_id, catalog.archive.scope.owner_id, catalog.archive.scope.trading_mode, operation)
        raise HTTPException(504, {"code": "dataset_catalog_timeout", "message": "固定数据目录读取超时；未返回空结果", "request_id": request_id, "retryable": True}) from None
    except JobError as error:
        logger.warning("request_id=%s user_id=%s mode=%s operation=%s error_type=%s", request_id, catalog.archive.scope.owner_id, catalog.archive.scope.trading_mode, operation, error.code)
        raise HTTPException(error.status, {"code": error.code, "message": error.message, "request_id": request_id, "retryable": error.status == 503}) from None
    except Exception as error:
        logger.error("request_id=%s user_id=%s mode=%s operation=%s error_type=%s", request_id, catalog.archive.scope.owner_id, catalog.archive.scope.trading_mode, operation, type(error).__name__)
        raise HTTPException(503, {"code": "dataset_catalog_unavailable", "message": "固定数据目录暂不可用；没有用空列表掩盖异常", "request_id": request_id, "retryable": True}) from None
    logger.info("request_id=%s user_id=%s mode=%s operation=%s elapsed_ms=%.1f writes=0 model_calls=0", request_id, catalog.archive.scope.owner_id, catalog.archive.scope.trading_mode, operation, (perf_counter() - started) * 1000)
    return result


@router.get("/availability", response_model=DatasetAvailability)
async def availability(request: Request, catalog=Depends(service)):
    return await execute(request, catalog, "availability", catalog.availability)


@router.get("/datasets", response_model=DatasetCatalogPage)
async def datasets(request: Request, query: Annotated[DatasetListQuery, Query()], catalog=Depends(service)):
    return await execute(request, catalog, "list", lambda: catalog.list(limit=query.limit, offset=query.offset))


@router.get("/datasets/{identifier}", response_model=DatasetDescription)
async def dataset(identifier: UUID, request: Request, query: Annotated[DatasetLookup, Query()], catalog=Depends(service)):
    reference = DatasetRef(dataset_id=identifier, data_version=query.data_version, qualification=query.qualification)
    return await execute(request, catalog, "describe", lambda: catalog.describe(reference))
