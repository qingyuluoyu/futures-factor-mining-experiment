"""A 股因子检验与组合回测 API —— 题目 2 核心端点

- GET  /api/stock/factors            因子目录（六类 9 个）
- POST /api/stock/factors/analysis   单因子检验：IC/IR、衰减、分组收益、多空净值
- POST /api/stock/backtest           组合回测：逐期调仓、成本、绩效、调仓明细
"""

import asyncio
import logging
from datetime import date
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.stock_research_jobs import create_job, service_dependency
from app.api.stock_custom_factors import router as custom_factor_router
from app.api.stock_ml_registry import router as ml_registry_router
from app.core.config import Settings
from app.schemas.stock_research_jobs import CreateResearchJob
from app.schemas.stock_research import AnalysisBody, BacktestBody, PreprocessBody, ResearchPeriodBody, _parse_date

from app.core.deps import AuthContext, get_auth_context, get_current_user, get_session, get_settings
from app.core.market_capability import require_stock_market
from app.models.user import User
from app.services.stock_factors.catalog import FACTOR_SPECS, factor_policy
from app.services.stock_factors.engine import (
    BacktestParams,
    METHOD_VERSION,
    MarketData,
    PreprocessConfig,
    ResearchDataError,
    analyze_factor,
    load_market_data,
    run_portfolio_backtest,
)

router = APIRouter(prefix="/api/stock", tags=["stock"], dependencies=[Depends(require_stock_market)])
router.include_router(custom_factor_router)
router.include_router(ml_registry_router)
logger = logging.getLogger("qihuo.stock_research")


async def _dispatch_research(
    request: Request, response: Response, ctx: AuthContext, session: AsyncSession,
    settings: Settings, body: ResearchPeriodBody, kind: str, prefer: str | None,
    idempotency_key: str | None, operation: str, calculation: Callable[..., dict[str, Any]],
    *arguments: Any,
) -> dict[str, Any]:
    headers = {"X-Request-ID": uuid4().hex, "Cache-Control": "no-store", "Link": '</api/stock/research/jobs>; rel="alternate"'}
    if prefer is not None or idempotency_key is not None:
        if prefer is None or prefer.strip().lower() != "respond-async" or not idempotency_key:
            raise HTTPException(422, "异步研究须同时提供Prefer: respond-async与UUID格式Idempotency-Key", headers=headers)
        try:
            command = TypeAdapter(CreateResearchJob).validate_python({"kind": kind, "idempotency_key": idempotency_key, "parameters": await request.json()})
        except ValidationError:
            raise HTTPException(422, "异步研究参数或幂等键无效；请使用研究任务Schema", headers=headers) from None
        service = await service_dependency(ctx=ctx, session=session, settings=settings)
        result = await create_job(command, request, response, service)
        response.status_code = 202
        response.headers["Preference-Applied"] = "respond-async"
        response.headers["Location"] = f"/api/stock/research/jobs/{result['id']}"
        return result
    if not settings.stock_legacy_sync_enabled:
        raise HTTPException(503, "旧同步研究已关闭，请显式协商异步或使用研究任务入口；未执行计算", headers={**headers, "X-Research-Error": "legacy_sync_disabled"})
    response.headers["X-Legacy-Research"] = "deprecated-sync"
    response.headers["Link"] = headers["Link"]
    response.headers["Cache-Control"] = "no-store"
    return await _execute_research(request, response, ctx.user, body, operation, calculation, *arguments)




async def _execute_research(
    request: Request,
    response: Response,
    user: User,
    body: ResearchPeriodBody,
    operation: str,
    calculation: Callable[..., dict[str, Any]],
    *arguments: Any,
) -> dict[str, Any]:
    request_id = uuid4().hex
    headers = {"X-Request-ID": request_id, "X-Method-Version": METHOD_VERSION}
    started = perf_counter()
    try:
        data = await load_market_data(request.app.state.session_factory, _parse_date(body.start_date), _parse_date(body.end_date, date.today()))
        result = await asyncio.to_thread(calculation, data, *arguments)
    except ResearchDataError as error:
        result = {"error": str(error), "error_code": error.code, "status_code": error.status}
    except Exception as error:
        logger.error("request_id=%s user_id=%s operation=%s error_type=%s elapsed_ms=%.1f", request_id, user.id, operation, type(error).__name__, (perf_counter() - started) * 1000)
        raise
    if result.get("error"):
        code = str(result.get("error_code", "invalid_research_data"))
        logger.warning("request_id=%s user_id=%s operation=%s error=%s elapsed_ms=%.1f", request_id, user.id, operation, code, (perf_counter() - started) * 1000)
        raise HTTPException(status_code=int(result.get("status_code", 422)), detail=result["error"], headers={**headers, "X-Research-Error": code})
    for header, value in headers.items():
        response.headers[header] = value
    logger.info("request_id=%s user_id=%s operation=%s method_version=%s elapsed_ms=%.1f", request_id, user.id, operation, METHOD_VERSION, (perf_counter() - started) * 1000)
    return result


@router.get("/factors")
async def list_factors(
    _user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """因子目录（价值/成长/质量/动量/波动/流动性）"""
    return [
        {
            "id": s.id,
            "label": s.label,
            "group": s.group,
            "formula": s.formula,
            "direction": s.direction,
            "source": s.source,
            "description": s.description,
            "method_version": METHOD_VERSION,
            **factor_policy(s.id),
        }
        for s in FACTOR_SPECS
    ]


@router.post("/factors/analysis", responses={202: {"description": "仅显式Prefer: respond-async时返回持久研究任务"}})
async def factor_analysis(
    body: AnalysisBody,
    request: Request,
    response: Response,
    prefer: str | None = Header(None, max_length=64),
    idempotency_key: str | None = Header(None, max_length=64),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """单因子检验：IC/IR、IC 衰减、分组收益、多空组合净值"""
    config = PreprocessConfig(**body.preprocess.model_dump())
    return await _dispatch_research(
        request, response, ctx, session, settings, body, "analysis", prefer, idempotency_key, "factor_analysis", analyze_factor,
        body.factor_id, config, body.horizon, 60, body.groups,
    )


@router.post("/backtest", responses={202: {"description": "仅显式Prefer: respond-async时返回持久研究任务"}})
async def stock_backtest(
    body: BacktestBody,
    request: Request,
    response: Response,
    prefer: str | None = Header(None, max_length=64),
    idempotency_key: str | None = Header(None, max_length=64),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """组合回测：逐期调仓（周/月）+ 成本模型 + 绩效指标 + 调仓明细"""
    params = BacktestParams(
        factor_ids=body.factor_ids,
        start_date=body.start_date,
        end_date=body.end_date,
        rebalance=body.rebalance,
        top_n=body.top_n,
        weight=body.weight,
        commission_rate=body.commission_rate,
        slippage_rate=body.slippage_rate,
        preprocess=PreprocessConfig(**body.preprocess.model_dump()),
    )
    return await _dispatch_research(request, response, ctx, session, settings, body, "backtest", prefer, idempotency_key, "portfolio_backtest", run_portfolio_backtest, params)
