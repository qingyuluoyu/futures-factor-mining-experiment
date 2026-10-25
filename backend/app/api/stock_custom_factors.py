import asyncio
import logging
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.deps import AuthContext, get_auth_context, get_session
from app.core.market_capability import require_stock_market
from app.schemas.stock_custom_factor import CompiledFactorView, CustomFactorCreate, CustomFactorDisable, CustomFactorEdit, CustomFactorPage, CustomFactorView, FactorExpression
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.custom_factors import ALLOWED_FIELDS, FIELD_SPECS, CustomFactorService, compiled_view, custom_factors_ready
from app.services.stock_research_v2.expressions import ExpressionError, compile_expression

logger = logging.getLogger("qihuo.stock_custom_factors")


class CustomFactorRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def traced(request: Request):
            request_id = uuid4().hex
            headers = {"X-Request-ID": request_id, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            request.state.stock_custom_request_id = request_id
            try:
                response = await handler(request)
            except RequestValidationError:
                raise HTTPException(422, {"code": "invalid_input", "message": "自定义因子请求字段、类型或长度无效", "request_id": request_id, "retryable": False}, headers=headers) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **headers}
                if isinstance(error.detail, dict):
                    error.detail["request_id"] = request_id
                else:
                    code = {401: "unauthenticated", 403: "forbidden", 409: "stock_required"}.get(error.status_code, "request_failed")
                    error.detail = {"code": code, "message": str(error.detail)[:500], "request_id": request_id, "retryable": False}
                raise
            except Exception as error:
                logger.error("request_id=%s operation=response_validation error_type=%s", request_id, type(error).__name__)
                raise HTTPException(503, {"code": "custom_factor_response_failed", "message": "因子响应无法安全验证；请保留原幂等键核对，不要重复新建", "request_id": request_id, "retryable": True}, headers=headers) from None
            response.headers.update(headers)
            return response

        return traced


router = APIRouter(prefix="/research/v2/custom-factors", tags=["A股自定义因子版本"], route_class=CustomFactorRoute, dependencies=[Depends(require_stock_market)])


async def service(ctx: AuthContext = Depends(get_auth_context), session=Depends(get_session)):
    return CustomFactorService(session, ctx.user.id, ctx.trading_mode)


async def execute(request, backend, operation, callback, *, write=False):
    request_id = request.state.stock_custom_request_id
    started = perf_counter()
    try:
        async with asyncio.timeout(8):
            result = await callback()
            if write:
                await backend.session.commit()
    except (JobError, ExpressionError) as error:
        await backend.session.rollback()
        code = error.code if isinstance(error, JobError) else "invalid_expression"
        status = error.status if isinstance(error, JobError) else 422
        message = error.message if isinstance(error, JobError) else str(error)
        logger.warning("request_id=%s user_id=%s mode=%s operation=%s error_type=%s", request_id, backend.owner_id, backend.mode, operation, code)
        raise HTTPException(status, {"code": code, "message": message, "request_id": request_id, "retryable": status == 503}) from None
    except Exception as error:
        await backend.session.rollback()
        logger.error("request_id=%s user_id=%s mode=%s operation=%s error_type=%s", request_id, backend.owner_id, backend.mode, operation, type(error).__name__)
        raise HTTPException(503, {"code": "custom_factor_service_failed", "message": "自定义因子服务暂不可用；写请求请保留原幂等键重试核对", "request_id": request_id, "retryable": True}) from None
    logger.info("request_id=%s user_id=%s mode=%s operation=%s elapsed_ms=%.1f model_calls=0", request_id, backend.owner_id, backend.mode, operation, (perf_counter() - started) * 1000)
    return result


@router.get("/availability")
async def availability(request: Request, backend=Depends(service)):
    ready = await execute(request, backend, "availability", lambda: custom_factors_ready(backend.session))
    return {"definitions_enabled": ready, "research_enabled": False, "reason": "定义管理可用；固定数据版本的IC与回测尚未接通" if ready else "自定义因子定义表未迁移或版本不匹配", "fields": list(FIELD_SPECS)}


@router.post("/validate", response_model=CompiledFactorView)
async def validate(body: FactorExpression, request: Request, ctx: AuthContext = Depends(get_auth_context)):
    try:
        return compiled_view(compile_expression(body.expression, allowed_fields=ALLOWED_FIELDS))
    except ExpressionError as error:
        raise HTTPException(422, {"code": "invalid_expression", "message": str(error), "request_id": request.state.stock_custom_request_id, "retryable": False}) from None


@router.get("", response_model=CustomFactorPage)
async def definitions(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), backend=Depends(service)):
    return await execute(request, backend, "list", lambda: backend.list(limit=limit, offset=offset))


@router.post("", response_model=CustomFactorView)
async def create(body: CustomFactorCreate, request: Request, backend=Depends(service)):
    return await execute(request, backend, "create", lambda: backend.mutate(body, request_id=request.state.stock_custom_request_id), write=True)


@router.put("/{identifier}", response_model=CustomFactorView)
async def edit(identifier: UUID, body: CustomFactorEdit, request: Request, backend=Depends(service)):
    return await execute(request, backend, "edit", lambda: backend.mutate(body, identifier=identifier, request_id=request.state.stock_custom_request_id), write=True)


@router.post("/{identifier}/disable", response_model=CustomFactorView)
async def disable(identifier: UUID, body: CustomFactorDisable, request: Request, backend=Depends(service)):
    return await execute(request, backend, "disable", lambda: backend.mutate(body, identifier=identifier, disable=True, request_id=request.state.stock_custom_request_id), write=True)


@router.get("/{identifier}/versions", response_model=CustomFactorPage)
async def versions(identifier: UUID, request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), backend=Depends(service)):
    return await execute(request, backend, "versions", lambda: backend.list(identifier=identifier, limit=limit, offset=offset))


@router.get("/{identifier}/versions/{revision}", response_model=CustomFactorView)
async def version(identifier: UUID, request: Request, revision: int = Path(..., ge=1, le=2147483647), backend=Depends(service)):
    return await execute(request, backend, "version", lambda: backend.get(identifier, revision))
