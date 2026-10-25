import asyncio
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.financial_request_route import FinancialRequestRoute
from app.schemas.stock_replay_history import ReplayHistoryQuery
from app.services.stock_replay import StockReplayService
from app.services.stock_replay_engine import ReplayError
from app.services.stock_replay_history import read_order_history


logger = logging.getLogger("qihuo.stock_replay_history")


class HistoryReadRoute(FinancialRequestRoute):
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


def make_history_router(replay_dependency):
    router = APIRouter(route_class=HistoryReadRoute)

    @router.get("/accounts/{account_id}/order-history")
    async def order_history(account_id: UUID, request: Request, response: Response, query: Annotated[ReplayHistoryQuery, Query()], service: StockReplayService = Depends(replay_dependency)) -> dict:
        request_id = request.state.request_id
        headers = {"X-Request-ID": request_id, "Cache-Control": "no-store"}
        user_id, mode = service.user_id, service.mode
        try:
            async with asyncio.timeout(12):
                if service.session.new or service.session.dirty or service.session.deleted:
                    raise ReplayError("history_dependency_not_readonly", "历史请求的鉴权事务存在待写变更，已拒绝查询", 503)
                await service.session.rollback()
                result = await read_order_history(request.app.state.session_factory, user_id, mode, account_id, query)
        except ReplayError as error:
            raise HTTPException(error.status, error.message, headers={**headers, "X-Replay-History-Error": error.code}) from None
        except Exception as error:
            logger.warning("request_id=%s user_id=%s mode=%s operation=stock_order_history error_type=%s", request_id, user_id, mode, type(error).__name__)
            raise HTTPException(503, "模拟委托历史读取失败，请凭请求编号核查；未返回假空表", headers=headers) from None
        response.headers.update(headers)
        logger.info("request_id=%s user_id=%s mode=%s operation=stock_order_history revision=%s returned=%s source_calls=0 model_calls=0", request_id, user_id, mode, result["revision"], len(result["items"]))
        return result

    return router
