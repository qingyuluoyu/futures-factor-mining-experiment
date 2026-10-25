import asyncio
from datetime import datetime
import logging
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.deps import AuthContext, get_auth_context
from app.core.financial_request_route import FinancialRequestRoute
from app.schemas.stock_fundamentals import FundamentalQuery
from app.services.stock_factors.engine import ResearchDataError
from app.services.stock_fundamentals import read_fundamentals


logger = logging.getLogger("qihuo.stock_fundamentals")


class FundamentalReadRoute(FinancialRequestRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def no_store(request):
            try:
                response = await original(request)
            except StarletteHTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response
        return no_store


router = APIRouter(route_class=FundamentalReadRoute)


@router.get("/fundamentals")
async def fundamentals(request: Request, response: Response, query: Annotated[FundamentalQuery, Query()], ctx: AuthContext = Depends(get_auth_context)) -> dict:
    request_id = request.state.request_id
    headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
    try:
        async with asyncio.timeout(12):
            result = await read_fundamentals(request.app.state.session_factory, query, today=datetime.now(ZoneInfo("Asia/Shanghai")).date())
    except ResearchDataError as error:
        raise HTTPException(error.status, str(error), headers={**headers, "X-Fundamental-Error": error.code}) from None
    except Exception as error:
        logger.warning("request_id=%s user_id=%s mode=%s operation=stock_fundamental_history error_type=%s", request_id, ctx.user.id, ctx.trading_mode, type(error).__name__)
        raise HTTPException(503, "估值与披露历史读取失败，请凭请求编号核查；未返回假空表", headers=headers) from None
    response.headers.update(headers)
    logger.info("request_id=%s user_id=%s mode=%s operation=stock_fundamental_history kind=%s returned=%s source_calls=0 model_calls=0", request_id, ctx.user.id, ctx.trading_mode, query.kind, len(result["rows"]))
    return result
