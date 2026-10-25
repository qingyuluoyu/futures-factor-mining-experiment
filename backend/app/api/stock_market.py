import logging
import asyncio
from datetime import datetime
from typing import Annotated, Literal
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.market_capability import require_stock_market
from app.models.user import User
from app.services.stock_market import StockMarketError, filter_members, load_market_snapshot, stock_details
from app.services.stock_eod_status import SHANGHAI, read_sync_status
from app.services.stock_archive_status import read_archive_status
from app.core.financial_request_route import FinancialRequestRoute
from app.schemas.stock_screen import StockScreenFilters
from app.services.stock_screen import StockScreenChangedError, screen_snapshot
from app.schemas.stock_eod_gaps import EodGapQuery
from app.services.stock_eod_gaps import GapSnapshotChanged, read_eod_gaps
from app.api.stock_fundamentals import router as fundamentals_router
from app.api.stock_archive_catalog import router as archive_catalog_router


router = APIRouter(prefix="/api/stock/market", tags=["A股板块行情"], dependencies=[Depends(require_stock_market)])
logger = logging.getLogger("qihuo.stock_market")
router.include_router(fundamentals_router)
router.include_router(archive_catalog_router)


async def snapshot_response(session: AsyncSession, response: Response, user_id: UUID | str, operation: str, *, prefer_eod: bool = False) -> dict:
    request_id = uuid4().hex
    started = perf_counter()
    response.headers["X-Request-ID"] = request_id
    try:
        snapshot = await load_market_snapshot(session, prefer_eod=prefer_eod)
    except StockMarketError as error:
        logger.warning("request_id=%s user_id=%s operation=%s error_type=invalid_market_snapshot", request_id, user_id, operation)
        raise HTTPException(409, str(error), headers={"X-Request-ID": request_id}) from error
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=%s error_type=%s", request_id, user_id, operation, type(error).__name__)
        raise HTTPException(503, "行情库读取失败，请稍后重试", headers={"X-Request-ID": request_id}) from None
    logger.info("request_id=%s user_id=%s operation=%s elapsed_ms=%.1f", request_id, user_id, operation, (perf_counter() - started) * 1000)
    return snapshot


def metadata(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key not in ("boards", "items")}


@router.get("/sync-status")
async def market_sync_status(request: Request, response: Response, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    request_id, started = uuid4().hex, perf_counter()
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    for name, value in headers.items():
        response.headers[name] = value
    state = request.app.state
    worker = getattr(state, "stock_eod_worker", None)
    task = getattr(worker, "task", None)
    try:
        async with asyncio.timeout(5):
            result = await read_sync_status(session, now=datetime.now(SHANGHAI),
                enabled=bool(getattr(state, "stock_eod_enabled", False)),
                sync_enabled=bool(getattr(state, "stock_eod_sync_enabled", False)),
                catchup_enabled=bool(getattr(state, "stock_eod_catchup_enabled", False)),
                worker_running=task is not None and not task.done(), error_code=getattr(state, "stock_eod_error_code", None))
            archive_task = getattr(getattr(state, "stock_archive_worker", None), "task", None)
            result["archive"] = await read_archive_status(session, now=datetime.now(SHANGHAI),
                enabled=bool(getattr(state, "stock_archive_enabled", False)), ready=bool(getattr(state, "stock_archive_ready", False)),
                worker_running=archive_task is not None and not archive_task.done())
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=stock_sync_status error_type=%s elapsed_ms=%.1f", request_id, user.id, type(error).__name__, (perf_counter() - started) * 1000)
        raise HTTPException(503, "盘后同步状态暂不可读取，请稍后重试", headers=headers) from None
    logger.info("request_id=%s user_id=%s operation=stock_sync_status state=%s elapsed_ms=%.1f source_calls=0", request_id, user.id, result["state"], (perf_counter() - started) * 1000)
    return result


@router.get("/boards")
async def market_boards(request: Request, response: Response, keyword: str = Query("", max_length=64), limit: int = Query(200, ge=1, le=200), offset: int = Query(0, ge=0, le=10000), session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    snapshot = await snapshot_response(session, response, user.id, "stock_boards", prefer_eod=getattr(request.app.state, "stock_eod_enabled", False))
    rows = [row for row in snapshot["boards"] if keyword.strip().casefold() in row["industry"].casefold()]
    return {**metadata(snapshot), "boards": rows[offset:offset + limit], "total": len(rows), "has_more": offset + limit < len(rows)}


@router.get("/members")
async def market_members(request: Request, response: Response, industry: str = Query("", max_length=32), keyword: str = Query("", max_length=64), sort: Literal["code", "name", "change_pct"] = "code", direction: Literal["asc", "desc"] = "asc", data_only: bool = False, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000), session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    snapshot = await snapshot_response(session, response, user.id, "stock_members", prefer_eod=getattr(request.app.state, "stock_eod_enabled", False))
    rows = filter_members(snapshot["items"], industry=industry, keyword=keyword, sort=sort, direction=direction, data_only=data_only)
    return {**metadata(snapshot), "items": rows[offset:offset + limit], "total": len(rows), "has_more": offset + limit < len(rows)}


@router.get("/details")
async def market_details(response: Response, code: str = Query(..., pattern=r"^\d{6}$"), session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    request_id = uuid4().hex
    started = perf_counter()
    response.headers["X-Request-ID"] = request_id
    try:
        details = await stock_details(session, code)
    except StockMarketError as error:
        logger.warning("request_id=%s user_id=%s operation=stock_details error_type=invalid_market_data", request_id, user.id)
        raise HTTPException(409, str(error), headers={"X-Request-ID": request_id}) from error
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=stock_details error_type=%s", request_id, user.id, type(error).__name__)
        raise HTTPException(503, "股票详情读取失败，请稍后重试", headers={"X-Request-ID": request_id}) from None
    if details is None:
        raise HTTPException(404, "证券不在当前已导入目录中", headers={"X-Request-ID": request_id})
    logger.info("request_id=%s user_id=%s operation=stock_details elapsed_ms=%.1f", request_id, user.id, (perf_counter() - started) * 1000)
    return details


async def market_screen(request: Request, response: Response, filters: Annotated[StockScreenFilters, Query()], session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    request_id = uuid4().hex
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(8):
            snapshot = await snapshot_response(session, response, user.id, "stock_screen", prefer_eod=getattr(request.app.state, "stock_eod_enabled", False))
            return screen_snapshot(snapshot, filters)
    except TimeoutError:
        logger.warning("request_id=%s user_id=%s operation=stock_screen error_type=timeout", request_id, user.id)
        raise HTTPException(503, "股票筛选读取超时，请重试；未以空数据替代失败", headers={"X-Request-ID": request_id, "Cache-Control": "no-store"}) from None
    except HTTPException as error:
        error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
        raise
    except StockScreenChangedError as error:
        raise HTTPException(409, str(error), headers={"X-Request-ID": request_id, "Cache-Control": "no-store", "X-Stock-Screen-Error": "snapshot_changed"}) from None
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=stock_screen error_type=%s", request_id, user.id, type(error).__name__)
        raise HTTPException(503, "股票筛选暂不可读取，请重试；未以空数据替代失败", headers={"X-Request-ID": request_id, "Cache-Control": "no-store"}) from None


router.add_api_route("/screen", market_screen, methods=["GET"], route_class_override=FinancialRequestRoute)


async def market_eod_gaps(request: Request, response: Response, query: Annotated[EodGapQuery, Query()], session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    request_id = getattr(request.state, "request_id", uuid4().hex)
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(8):
            result = await read_eod_gaps(session, query, enabled=bool(getattr(request.app.state, "stock_eod_enabled", False)), today=datetime.now(SHANGHAI).date())
    except GapSnapshotChanged as error:
        raise HTTPException(409, str(error), headers={**headers, "X-Stock-Gap-Error": "snapshot_changed"}) from None
    except Exception as error:
        logger.warning("request_id=%s user_id=%s operation=stock_eod_gaps error_type=%s source_calls=0", request_id, user.id, type(error).__name__)
        raise HTTPException(503, "盘后缺口数据读取或校验失败，请凭请求编号核查；未以空清单替代失败", headers=headers) from None
    for name, value in headers.items():
        response.headers[name] = value
    logger.info("request_id=%s user_id=%s operation=stock_eod_gaps state=%s total=%s source_calls=0", request_id, user.id, result["state"], result["total"])
    return result


class StockEodGapRoute(FinancialRequestRoute):
    def get_route_handler(self):
        traced = super().get_route_handler()

        async def no_store(request):
            try:
                response = await traced(request)
            except StarletteHTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return no_store


router.add_api_route("/eod-gaps", market_eod_gaps, methods=["GET"], route_class_override=StockEodGapRoute)
