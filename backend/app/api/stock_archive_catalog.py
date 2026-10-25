import asyncio
from datetime import datetime, timezone
import logging
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.financial_request_route import FinancialRequestRoute
from app.core.market_capability import require_stock_market
from app.models.user import User
from app.schemas.stock_archive_catalog import ArchiveCatalogQuery
from app.services.stock_archive_catalog import ArchiveCatalogChanged, read_archive_catalog
from app.schemas.stock_archive_observations import ArchiveObservationQuery
from app.services.stock_raw_panel import RawPanelBusy, RawPanelVersionNotFound, read_raw_panel

logger = logging.getLogger("qihuo.stock_archive_catalog")
observation_gate = asyncio.BoundedSemaphore(2)


class CatalogRoute(FinancialRequestRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def no_store(request):
            headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
            try:
                response = await handler(request)
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **headers}
                raise
            for name, value in headers.items():
                response.headers[name] = value
            return response
        return no_store


router = APIRouter(route_class=CatalogRoute, dependencies=[Depends(require_stock_market)])


@router.get("/archive/versions")
async def archive_versions(request: Request, response: Response, query: Annotated[ArchiveCatalogQuery, Query()],
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    request_id = request.state.request_id
    if not getattr(request.app.state, "stock_archive_ready", False):
        raise HTTPException(503, "原价影子归档尚未就绪；不代表归档目录为空")
    now = datetime.now(timezone.utc)
    cutoff = query.snapshot_at or now
    if cutoff > now or (query.trade_date is not None and query.trade_date > cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date()):
        raise HTTPException(422, "目录日期和快照不能晚于读取时点")
    try:
        async with asyncio.timeout(8):
            result = await read_archive_catalog(session, query, now=now)
    except ArchiveCatalogChanged as error:
        logger.info("request_id=%s user_id=%s operation=stock_archive_catalog error_type=snapshot_changed source_calls=0 model_calls=0", request_id, user.id)
        raise HTTPException(409, str(error), headers={"X-Stock-Archive-Error": "snapshot_changed"}) from None
    except Exception as error:
        logger.warning("request_id=%s user_id=%s operation=stock_archive_catalog error_type=%s source_calls=0 model_calls=0", request_id, user.id, type(error).__name__)
        raise HTTPException(503, "归档目录读取或校验失败，请凭请求编号核查；未以空清单替代失败") from None
    response.headers["X-Request-ID"] = request_id
    logger.info("request_id=%s user_id=%s operation=stock_archive_catalog total=%s source_calls=0 model_calls=0", request_id, user.id, result["total"])
    return result


@router.get("/archive/versions/{version}/observation")
async def archive_observation(request: Request, response: Response, query: Annotated[ArchiveObservationQuery, Query()],
    version: str = Path(pattern=r"^[a-f0-9]{64}$"), session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    request_id = request.state.request_id
    if not getattr(request.app.state, "stock_archive_ready", False):
        raise HTTPException(503, "原价影子归档尚未就绪；未读取最新行情替代")
    if observation_gate.locked():
        raise HTTPException(429, "同时进行的归档核对已满，请稍后显式重试；未排队或重复核对")
    async with observation_gate:
        try:
            async with asyncio.timeout(8):
                result = await read_raw_panel(session, [version], [query.instrument_id], now=datetime.now(timezone.utc))
        except RawPanelVersionNotFound:
            raise HTTPException(404, "指定归档版本不存在；未自动改读最新版本") from None
        except RawPanelBusy:
            raise HTTPException(429, "归档校验线程仍忙，请稍后显式重试；未排队或重复核对") from None
        except TimeoutError:
            raise HTTPException(504, "固定版本核对超时，未返回部分价量，请稍后显式重试") from None
        except ValueError:
            raise HTTPException(409, "已存来源与归档内容不一致或无法校验；未返回未经核对的价量") from None
        except Exception as error:
            logger.warning("request_id=%s user_id=%s operation=stock_archive_observation error_type=%s source_calls=0 model_calls=0", request_id, user.id, type(error).__name__)
            raise HTTPException(503, "归档读取失败，请凭请求编号核查；未以零价量或空成功替代失败") from None
    response.headers["X-Request-ID"] = request_id
    logger.info("request_id=%s user_id=%s operation=stock_archive_observation versions=1 instruments=1 output_rows=%s source_calls=0 model_calls=0", request_id, user.id, len(result.get("observations", [])))
    return result
