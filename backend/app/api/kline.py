"""K 线历史数据 REST API

提供 GET /api/market/kline?symbol=&period= 端点，
参数校验 + 缓存优先读取；forming bar 按 JWT trading_mode 分盘。
另提供 GET /api/market/kline/bundle 多周期打包端点（分时切换秒开）。
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import decode_token
from app.core.config import load_settings
from app.core.deps import AuthContext, get_auth_context, get_session
from app.core.trading_mode import DEFAULT_MODE, normalize_trading_mode
from app.data.sina_kline import SUPPORTED_PERIODS
from app.schemas.kline import KlineBundleResponseSchema, KlineResponseSchema
from app.services.kline import get_kline_history

logger = logging.getLogger("qihuo.api.kline")

router = APIRouter(prefix="/api/market", tags=["market"])
_optional_bearer = HTTPBearer(auto_error=False)

# bundle 固定周期集合：全部分钟周期。1d（数据源/深历史链路不同）与
# tick 不打包，客户端各自按单周期端点取。
BUNDLE_PERIODS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "60m")


def _mode_from_token(
    creds: HTTPAuthorizationCredentials | None,
) -> str:
    """从可选 Bearer 解析 trading_mode；无 token 默认 live"""
    if creds is None or not creds.credentials:
        return DEFAULT_MODE
    settings = load_settings()
    try:
        payload = decode_token(settings, creds.credentials)
        if payload.get("type") != "access":
            return DEFAULT_MODE
        return normalize_trading_mode(
            str(payload.get("trading_mode") or DEFAULT_MODE)
        )
    except JWTError:
        return DEFAULT_MODE


@router.get("/kline", response_model=KlineResponseSchema)
async def kline_endpoint(
    request: Request,
    symbol: str = Query(..., description="合约代码，如 rb2610"),
    period: str = Query(..., description="K 线周期：1m/5m/15m/30m/60m/1d"),
    limit: int = Query(0, description="返回最多 N 根 bar，0 表示全部"),
    end_time: str = Query("", description="只返回 time < end_time 的 bar，用于懒加载"),
    session: AsyncSession = Depends(get_session),
    creds: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> KlineResponseSchema:
    """获取 K 线历史数据（缓存优先；virtual 会话合并 virtual forming）"""
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少 symbol 参数")
    if period not in SUPPORTED_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的周期: {period}，支持: {', '.join(SUPPORTED_PERIODS)}",
        )

    trading_mode = _mode_from_token(creds)
    redis_conn = request.app.state.redis
    bars = await get_kline_history(
        redis_conn,
        symbol,
        period,
        limit=limit,
        end_time=end_time,
        session=session,
        trading_mode=trading_mode,
    )

    has_more = False
    if limit > 0 and len(bars) >= limit:
        has_more = True
    elif period == "1d" and session is not None and not end_time:
        has_more = True

    return KlineResponseSchema(
        symbol=symbol,
        period=period,
        bars=bars,
        has_more=has_more,
    )


@router.get("/kline/bundle", response_model=KlineBundleResponseSchema)
async def kline_bundle_endpoint(
    request: Request,
    symbol: str = Query(..., description="合约代码，如 rb2610"),
    limit: int = Query(200, description="每周期返回最多 N 根 bar，0 表示全部"),
    session: AsyncSession = Depends(get_session),
    creds: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> KlineBundleResponseSchema:
    """一次返回同合约全部分钟周期最新视图（分时切换秒开）

    五周期并行走 get_kline_history 完整链路（缓存优先、新鲜度/价格
    守卫、分钟深历史补全、写各自 Redis 历史缓存）——本请求本身即把
    五周期缓存写热，后续单周期请求直接命中。单周期异常归一为空
    bars，不影响其余周期。
    """
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少 symbol 参数")

    trading_mode = _mode_from_token(creds)
    redis_conn = request.app.state.redis

    async def _fetch_one(period: str) -> list[dict[str, Any]]:
        try:
            return await get_kline_history(
                redis_conn,
                symbol,
                period,
                limit=limit,
                session=session,
                trading_mode=trading_mode,
            )
        except Exception:
            logger.exception("bundle 单周期取数失败 %s %s", symbol, period)
            return []

    bars_by_period = await asyncio.gather(
        *[_fetch_one(p) for p in BUNDLE_PERIODS]
    )

    periods = []
    for period, bars in zip(BUNDLE_PERIODS, bars_by_period):
        has_more = limit > 0 and len(bars) >= limit
        periods.append(
            {"period": period, "bars": bars, "has_more": has_more}
        )

    return KlineBundleResponseSchema(symbol=symbol, periods=periods)


@router.post("/kline/verify-repair")
async def verify_repair_kline(
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    """启动对账修正：近 3 天主力 × 全周期，主 PG 权威 vs Redis 缓存。

    桌面端每次启动时调用：重叠键 OHLCV 差异用 PG 权威值覆盖、
    缓存缺已收盘键补齐；完成后返回统计摘要。幂等可重入。
    """
    from app.services.kline_repair import verify_repair_kline_cache

    redis_conn = getattr(request.app.state, "redis", None)
    result = await verify_repair_kline_cache(session, redis_conn)
    return result


@router.get("/kline/prewarm/status")
async def kline_prewarm_status_endpoint(request: Request) -> dict[str, Any]:
    """盘前预热进度（done/total/last_error；openspec kline-prewarm）。

    状态读 Redis（backend 多 worker：预热在选主 worker，端点可能打到
    任意 worker），Redis 不可用回退本进程快照。
    """
    from app.services.kline_prewarm import prewarm_status

    redis_conn = getattr(request.app.state, "redis", None)
    return await prewarm_status(redis_conn)
