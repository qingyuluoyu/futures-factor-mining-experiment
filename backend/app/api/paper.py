"""模拟交易 API —— 虚拟资金、交易参数、委托、持仓（按 JWT trading_mode 分盘）"""

from uuid import UUID, uuid4
from decimal import Decimal
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.core.financial_request_route import FinancialRequestRoute
from app.core.market_capability import require_futures_market
from app.schemas.instrument_scope import CnyFuturesSymbol
from app.schemas.paper import (
    DepositFundsRequest,
    EstimateRequest,
    PlaceOrderRequest,
    UpdateTradeSettingsRequest,
)
from app.services.paper.account_svc import (
    claim_monthly_funds,
    deposit_custom_funds,
    get_account_summary,
    list_ledgers,
)
from app.services.paper.order_fill import calc_fee, calc_margin
from app.services.paper.price_band import (
    PRICE_BAND_RATIO,
    price_band_bounds,
    resolve_last_price,
)
from app.services.paper.specs import (
    ensure_trade_settings,
    list_product_specs_public,
    product_spec_public,
    resolve_trade_params,
    settings_to_dict,
    update_trade_settings,
)
from app.services.paper.order_query import cancel_order, list_orders, list_positions
from app.services.paper.trading_svc import place_order

router = APIRouter(prefix="/api/paper", tags=["模拟交易"], route_class=FinancialRequestRoute, dependencies=[Depends(require_futures_market)])
logger = logging.getLogger("qihuo.paper.funds")


@router.get("/account")
async def paper_account(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """获取模拟账户概览，兼容旧领取字段"""
    data = await get_account_summary(
        session, ctx.user.id, ctx.trading_mode
    )
    data["trading_mode"] = ctx.trading_mode
    from app.services.paper.valuation import account_valuation

    data["valuation"] = (await account_valuation(session, ctx.user.id, data, getattr(request.app.state, "redis", None), ctx.trading_mode)).model_dump(mode="json")
    logger.info("paper_valuation user_id=%s mode=%s status=%s issue_count=%s request_id=%s", ctx.user.id, ctx.trading_mode, data["valuation"]["status"], len(data["valuation"]["issues"]), getattr(request.state, "request_id", None))
    return data


@router.post("/account/claim")
async def paper_claim(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """兼容旧版领取接口，独立遵循历史周期与额度设置"""
    return await claim_monthly_funds(
        session, ctx.user.id, ctx.trading_mode
    )


@router.post("/account/deposit")
async def paper_deposit(
    body: DepositFundsRequest,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    logger.info("paper_custom_deposit_start user_id=%s mode=%s request_id=%s", ctx.user.id, ctx.trading_mode, request_id)
    try:
        data = await deposit_custom_funds(session, ctx.user.id, Decimal(body.amount), body.idempotency_key, ctx.trading_mode)
    except HTTPException as exc:
        logger.info("paper_custom_deposit_rejected user_id=%s mode=%s status=%s request_id=%s", ctx.user.id, ctx.trading_mode, exc.status_code, request_id)
        raise
    data["trading_mode"] = ctx.trading_mode
    data["request_id"] = request_id
    logger.info("paper_custom_deposit_prepared user_id=%s mode=%s deposit_id=%s replayed=%s request_id=%s", ctx.user.id, ctx.trading_mode, data["deposit_id"], data["replayed"], request_id)
    return data


@router.get("/ledgers")
async def paper_ledgers(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """资金流水"""
    return await list_ledgers(
        session, ctx.user.id, limit, offset, ctx.trading_mode
    )


@router.get("/orders")
async def paper_orders(
    status: str | None = Query(default=None, description="pending/filled/cancelled"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """委托列表"""
    return await list_orders(
        session, ctx.user.id, status, limit, offset, ctx.trading_mode
    )


@router.post("/orders")
async def paper_place_order(
    body: PlaceOrderRequest,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """模拟下单（市价立即成交，限价挂单；价格须在最新价±10%）"""
    redis_conn = getattr(request.app.state, "redis", None)
    return await place_order(
        session,
        ctx.user.id,
        body.symbol,
        body.direction,
        body.offset,
        body.order_type,
        body.price,
        body.quantity,
        body.symbol_name,
        body.multiplier,
        body.margin_rate,
        redis_conn,
        ctx.trading_mode,
        body.source,
    )


@router.delete("/orders/{order_id}")
async def paper_cancel_order(
    order_id: UUID,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """撤单"""
    return await cancel_order(
        session, ctx.user.id, order_id, ctx.trading_mode
    )


@router.get("/positions")
async def paper_positions(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """当前持仓"""
    return await list_positions(session, ctx.user.id, ctx.trading_mode)


@router.get("/product-specs")
async def paper_product_specs(
    ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    """全部品种默认交易规格（乘数/保证金/手续费）"""
    _ = ctx
    return {"items": list_product_specs_public()}


@router.get("/product-specs/{symbol}")
async def paper_product_spec_one(
    symbol: CnyFuturesSymbol,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """单品种：默认规格 + 叠加用户设置后的实际参数"""
    settings = await ensure_trade_settings(
        session, ctx.user.id, ctx.trading_mode
    )
    base = product_spec_public(symbol)
    resolved = resolve_trade_params(symbol, settings)
    return {
        "default": base,
        "effective": {
            "code": resolved["code"],
            "name": resolved["name"],
            "multiplier": resolved["multiplier"],
            "margin_rate": float(resolved["margin_rate"]),
            "fee_mode": resolved["fee_mode"],
            "open_fee": float(resolved["open_fee"]),
            "close_fee": float(resolved["close_fee"]),
        },
        "user_settings": settings_to_dict(settings),
    }


@router.post("/estimate")
async def paper_estimate(
    body: EstimateRequest,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """预估开/平仓保证金与手续费，并返回 ±10% 价格带"""
    if body.offset not in ("open", "close"):
        raise HTTPException(status_code=400, detail="offset 必须为 open 或 close")
    settings = await ensure_trade_settings(
        session, ctx.user.id, ctx.trading_mode
    )
    params = resolve_trade_params(body.symbol, settings)
    from decimal import Decimal

    price = Decimal(str(body.price))
    redis_conn = getattr(request.app.state, "redis", None)
    last_ref = await resolve_last_price(
        body.symbol, redis_conn, None, ctx.trading_mode, allow_mock=False
    )
    if last_ref is not None and last_ref > 0:
        lo, hi = price_band_bounds(last_ref)
        in_band = lo <= price <= hi
        band = {
            "last_price": float(last_ref),
            "band_ratio": float(PRICE_BAND_RATIO),
            "min_price": float(lo),
            "max_price": float(hi),
            "in_band": in_band,
            "enforced": True,
        }
    else:
        band = {
            "last_price": 0.0,
            "band_ratio": float(PRICE_BAND_RATIO),
            "min_price": 0.0,
            "max_price": 0.0,
            "in_band": True,
            "enforced": False,
        }

    mult = int(params["multiplier"])
    rate = params["margin_rate"]
    fee_mode = str(params["fee_mode"])
    fee_value = params["close_fee"] if body.offset == "close" else params["open_fee"]
    margin = calc_margin(price, body.quantity, mult, rate)
    fee = calc_fee(price, body.quantity, mult, fee_mode, fee_value)
    notional = price * body.quantity * mult
    return {
        "symbol": body.symbol.strip().lower(),
        "offset": body.offset,
        "price": float(price),
        "quantity": body.quantity,
        "multiplier": mult,
        "margin_rate": float(rate),
        "fee_mode": fee_mode,
        "fee_value": float(fee_value),
        "notional": float(notional),
        "margin": float(margin),
        "fee": float(fee),
        "total_needed": float(margin + fee) if body.offset == "open" else float(fee),
        "price_band": band,
    }


@router.get("/settings")
async def paper_get_settings(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """读取用户交易参数（保证金/手续费倍率与覆盖）"""
    row = await ensure_trade_settings(
        session, ctx.user.id, ctx.trading_mode
    )
    return settings_to_dict(row)


@router.put("/settings")
async def paper_put_settings(
    body: UpdateTradeSettingsRequest,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """更新用户交易参数"""
    try:
        return await update_trade_settings(
            session,
            ctx.user.id,
            body.margin_scale,
            body.fee_scale,
            body.margin_rate_override,
            body.fee_mode_override,
            body.open_fee_override,
            body.close_fee_override,
            body.clear_overrides,
            ctx.trading_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
