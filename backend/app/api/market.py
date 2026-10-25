"""行情 REST API —— 合约搜索、列表与行情筛选"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.config import load_settings
from app.core.deps import AuthContext, get_auth_context, get_current_user, get_session
from app.core.trading_mode import DEFAULT_MODE, MODE_VIRTUAL, normalize_trading_mode
from app.models.user import User
from app.schemas.market import ContractSchema, ContractSearchResponse
from app.schemas.screener import ScreenerRequest, ScreenerResponse
from app.services.market import get_contracts_from_cache
from app.services.market_screener import run_screener
from app.services.trading_hours import get_session_status, is_trading_hours

router = APIRouter(prefix="/api/market", tags=["行情"])
_optional_bearer = HTTPBearer(auto_error=False)


def _mode_from_token(
    creds: HTTPAuthorizationCredentials | None,
) -> str:
    if creds is None or not creds.credentials:
        return DEFAULT_MODE
    try:
        payload = decode_token(load_settings(), creds.credentials)
        if payload.get("type") != "access":
            return DEFAULT_MODE
        return normalize_trading_mode(
            str(payload.get("trading_mode") or DEFAULT_MODE)
        )
    except JWTError:
        return DEFAULT_MODE


@router.get("/channel-status")
async def public_channel_status(request: Request) -> dict[str, Any]:
    """公开：当前生效行情渠道（供前端工作台 badge，无需登录）"""
    redis_conn = getattr(request.app.state, "redis", None)
    active: str | None = None
    if redis_conn is not None:
        raw = await redis_conn.get("market:live:active_channel")
        if raw is not None:
            active = (
                raw.decode("utf-8", errors="ignore")
                if isinstance(raw, bytes)
                else str(raw)
            )
    return {"active_channel": active or "sina"}


@router.get("/quotes")
async def list_quotes(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> list[dict[str, Any]]:
    """HTTP 行情快照（侧栏/品种树首屏兜底；实时仍靠 WebSocket）

    按 JWT trading_mode 选 live/virtual 命名空间；live 时与 WS 一致：
    SimNow 最新交易日 ≥ sina 才用 live 前缀，否则回落 legacy sina。
    """
    import json as _json

    from app.services.channel_health import max_trade_date
    from app.services.market_keys import quote_scan_pattern

    mode = _mode_from_token(creds)
    redis_conn = getattr(request.app.state, "redis", None)
    if redis_conn is None:
        return []

    async def _scan(pattern: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for key in redis_conn.scan_iter(pattern):
            raw = await redis_conn.get(key)
            if not raw:
                continue
            try:
                out.append(_json.loads(raw))
            except (TypeError, ValueError):
                continue
        return out

    if mode == MODE_VIRTUAL:
        return await _scan(quote_scan_pattern(MODE_VIRTUAL))
    mode_q = await _scan(quote_scan_pattern("live"))
    legacy_q = await _scan("market:quote:*")
    if max_trade_date(mode_q) >= max_trade_date(legacy_q):
        return mode_q
    return legacy_q


@router.get("/contracts/search", response_model=ContractSearchResponse)
async def search_contracts(
    request: Request,
    q: str = Query(default="", description="搜索关键词"),
) -> ContractSearchResponse:
    """搜索合约：按合约代码或名称模糊匹配

    - q 为空时返回全部合约
    - 支持合约代码前缀匹配和名称包含匹配
    """
    redis_conn = request.app.state.redis
    all_contracts = await get_contracts_from_cache(redis_conn)

    if not q:
        contracts = [
            ContractSchema(symbol=c["symbol"], name=c["name"], exchange=c["exchange"])
            for c in all_contracts
        ]
        return ContractSearchResponse(contracts=contracts, total=len(contracts))

    keyword = q.lower()
    matched = [
        ContractSchema(symbol=c["symbol"], name=c["name"], exchange=c["exchange"])
        for c in all_contracts
        if keyword in c["symbol"].lower() or keyword in c["name"].lower()
    ]
    return ContractSearchResponse(contracts=matched, total=len(matched))


@router.get("/contracts", response_model=list[ContractSchema])
async def list_contracts(
    request: Request,
    exchange: str = Query(default="", description="交易所筛选，如 SHFE"),
) -> list[ContractSchema]:
    """获取全部合约列表，可选按交易所筛选"""
    redis_conn = request.app.state.redis
    all_contracts = await get_contracts_from_cache(redis_conn)

    if not exchange:
        return [
            ContractSchema(symbol=c["symbol"], name=c["name"], exchange=c["exchange"])
            for c in all_contracts
        ]

    exchange_lower = exchange.lower()
    exchange_cn = {
        "shfe": "上期所",
        "dce": "大商所",
        "czce": "郑商所",
        "cffex": "中金所",
        "gfex": "广期所",
        "ine": "能源中心",
    }.get(exchange_lower, exchange)

    return [
        ContractSchema(symbol=c["symbol"], name=c["name"], exchange=c["exchange"])
        for c in all_contracts
        if c["exchange"] == exchange_cn
    ]


@router.get("/session-status")
async def market_session_status(
    request: Request,
    symbol: str = Query(default="rb2610", description="合约代码，如 rb2610"),
    creds: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> dict:
    """查询合约当前是否处于可交易时段

    virtual 会话：7×24 视为可交易；并附带 openctp 是否有 virtual 报价。
    live 会话：国内交易日历。
    """
    mode = _mode_from_token(creds)
    sym = (symbol or "rb2610").strip().lower()
    if mode == MODE_VIRTUAL:
        has_quote = False
        last_price = None
        redis_conn = getattr(request.app.state, "redis", None)
        if redis_conn is not None:
            try:
                from app.services.market_keys import get_quote_json

                q = await get_quote_json(redis_conn, MODE_VIRTUAL, sym)
                if q and float(q.get("last_price") or 0) > 0:
                    has_quote = True
                    last_price = q.get("last_price")
            except Exception:
                pass
        msg = (
            "虚拟盘 7×24 可交易"
            if has_quote
            else "虚拟盘 7×24 可挂单，但 openctp 暂无该合约行情"
        )
        return {
            "symbol": sym,
            "product_code": "",
            "profile": "virtual_724",
            "profile_label": "虚拟盘 7×24",
            "is_open": True,
            "reason": "虚拟盘7x24",
            "message": msg,
            "trading_mode": MODE_VIRTUAL,
            "market_coarse_open": True,
            "has_virtual_quote": has_quote,
            "last_price": last_price,
            "sessions": [],
        }

    status = get_session_status(sym)
    status["market_coarse_open"] = is_trading_hours()
    status["trading_mode"] = "live"
    status["has_virtual_quote"] = None
    return status


@router.post("/screener", response_model=ScreenerResponse)
async def market_screener(
    body: ScreenerRequest,
    request: Request,
    ctx: AuthContext = Depends(get_auth_context),
) -> ScreenerResponse:
    """行情筛选：全市场主力 · 多指标条件组合（AND）· 点击触发。

    支持 MACD / 均线 / KDJ / RSI / BOLL 五类条件，
    参数与 window（最近 N 根收盘K内出现）可调。
    """
    redis_conn = getattr(request.app.state, "redis", None)
    payload = await run_screener(
        redis_conn,
        body.period,
        [c.model_dump() for c in body.conditions],
        trading_mode=ctx.trading_mode,
    )
    return ScreenerResponse.model_validate(payload)
