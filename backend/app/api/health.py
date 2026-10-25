"""健康检查路由 —— 进程存活 + 行情分盘摘要"""

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> dict[str, Any]:
    """健康检查：status 始终 ok 表示进程存活；market 为行情分盘摘要"""
    result: dict[str, Any] = {"status": "ok"}
    redis_conn = getattr(request.app.state, "redis", None)
    if redis_conn is None:
        result["market"] = {"ok": False, "error": "redis_not_ready"}
        return result
    try:
        from app.services.market_health import collect_market_health

        result["market"] = await collect_market_health(redis_conn)
    except Exception as exc:  # noqa: BLE001 — 健康检查不可因行情失败 500
        result["market"] = {"ok": False, "error": type(exc).__name__}
    return result
