import logging
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.core.market_capability import require_stock_market
from app.models.stock_replay import StockReplayDataset
from app.schemas.stock_replay import CreateReplayAccount, ReplayMutation, SubmitReplayOrder
from app.services.stock_replay import StockReplayService
from app.services.stock_replay_engine import ReplayError
from app.api.stock_replay_history import make_history_router


router = APIRouter(prefix="/api/stock/replay", tags=["A股日线回放"], dependencies=[Depends(require_stock_market)])
logger = logging.getLogger("qihuo.stock_replay")


async def ready(session: AsyncSession, settings: Settings) -> bool:
    if not settings.stock_replay_enabled:
        return False
    exists = await session.scalar(text("SELECT to_regclass('stock_replay_schema_versions')"))
    if exists is None:
        return False
    return bool(await session.scalar(text("SELECT 1 FROM stock_replay_schema_versions WHERE version = '20261017_001_stock_replay'")))


async def replay_service(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StockReplayService:
    if not await ready(session, settings):
        raise HTTPException(503, detail="日线回放未启用或尚未执行版本迁移")
    return StockReplayService(session, ctx.user.id, ctx.trading_mode)


@router.get("/availability")
async def availability(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not await ready(session, settings):
        return {"enabled": False, "datasets": []}
    rows = (await session.execute(select(StockReplayDataset.id, StockReplayDataset.code, StockReplayDataset.instrument_id, StockReplayDataset.name, StockReplayDataset.start_date, StockReplayDataset.end_date, StockReplayDataset.version, StockReplayDataset.meta).order_by(StockReplayDataset.created_at.desc(), StockReplayDataset.id).limit(100))).all()
    return {"enabled": True, "datasets": [{"id": str(row.id), "code": row.code, "instrument_id": row.instrument_id, "name": row.name, "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(), "version": row.version, "source": row.meta["supplier"], "adjustment": "none"} for row in rows]}


async def execute(request: Request, response: Response, service: StockReplayService, operation: str, callback) -> dict:
    request_id = uuid4().hex
    response.headers["X-Request-ID"] = request_id
    started = perf_counter()
    try:
        result = await callback()
        await service.session.commit()
    except ReplayError as error:
        await service.session.rollback()
        logger.warning("request_id=%s user_id=%s mode=%s operation=%s error=%s elapsed_ms=%.1f", request_id, service.user_id, service.mode, operation, error.code, (perf_counter() - started) * 1000)
        raise HTTPException(error.status, detail=error.message, headers={"X-Request-ID": request_id}) from error
    except Exception as error:
        await service.session.rollback()
        logger.error("request_id=%s user_id=%s mode=%s operation=%s error_type=%s elapsed_ms=%.1f", request_id, service.user_id, service.mode, operation, type(error).__name__, (perf_counter() - started) * 1000)
        raise HTTPException(500, detail="回放处理失败，事务已回滚；请刷新核对状态或使用原幂等键重试", headers={"X-Request-ID": request_id}) from None
    logger.info("request_id=%s user_id=%s mode=%s operation=%s elapsed_ms=%.1f", request_id, service.user_id, service.mode, operation, (perf_counter() - started) * 1000)
    return result


@router.get("/accounts")
async def list_accounts(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000), service: StockReplayService = Depends(replay_service)) -> list[dict]:
    return await service.list_accounts(limit, offset)


@router.post("/accounts")
async def create_account(body: CreateReplayAccount, request: Request, response: Response, service: StockReplayService = Depends(replay_service)) -> dict:
    return await execute(request, response, service, "create", lambda: service.create(body))


@router.get("/accounts/{account_id}")
async def get_account(account_id: UUID, request: Request, response: Response, code: str | None = Query(None, pattern=r"^(?:60|00)[0-9]{4}$"), service: StockReplayService = Depends(replay_service)) -> dict:
    async def read():
        return await service.view(await service.account(account_id, lock=True), code)
    return await execute(request, response, service, "read", read)


@router.post("/accounts/{account_id}/orders")
async def submit_order(account_id: UUID, body: SubmitReplayOrder, request: Request, response: Response, service: StockReplayService = Depends(replay_service)) -> dict:
    return await execute(request, response, service, "order", lambda: service.submit(account_id, body))


@router.post("/accounts/{account_id}/orders/{order_id}/cancel")
async def cancel_order(account_id: UUID, order_id: UUID, body: ReplayMutation, request: Request, response: Response, service: StockReplayService = Depends(replay_service)) -> dict:
    return await execute(request, response, service, "cancel", lambda: service.mutate(account_id, body, cancel_id=order_id))


@router.post("/accounts/{account_id}/step")
async def advance(account_id: UUID, body: ReplayMutation, request: Request, response: Response, service: StockReplayService = Depends(replay_service)) -> dict:
    return await execute(request, response, service, "advance", lambda: service.mutate(account_id, body))


router.include_router(make_history_router(replay_service))
