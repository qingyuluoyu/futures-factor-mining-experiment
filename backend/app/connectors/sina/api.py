"""sina-bridge HTTP 服务 —— K 线/单合约 quote 的按需代理面

backend 进程（kline_provider 的 sina 主源、AI 工具 quote 兜底）与运维
脚本经此访问新浪，出网仍集中在桥容器内。仅 compose 内网可达，无鉴权
（不绑宿主端口）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import bridge
from .config import BRIDGE_PORT
from .guard import BridgeBusy, BridgeUpstreamError, KlineGuard

logger = logging.getLogger("qihuo.sina_bridge")


async def _guarded_kline(
    app: FastAPI, kind: str, symbol: str, period: str
) -> tuple[list[dict[str, Any]], bool]:
    """经守卫取 K 线；排队超预算/上游故障时过期副本兜底，无副本 503"""
    guard: KlineGuard = app.state.kline_guard
    try:
        return await guard.fetch(kind, symbol, period)
    except (BridgeBusy, BridgeUpstreamError) as exc:
        stale = guard.stale_copy(kind, symbol, period)
        if stale is not None:
            logger.warning("sina 桥 %s %s 降级过期副本（%s）", kind, symbol, exc)
            return stale, True
        raise HTTPException(status_code=503, detail=f"sina bridge: {exc}")


def _bars_response(bars: list[dict[str, Any]], stale: bool) -> JSONResponse:
    headers = {"X-Bridge-Stale": "1"} if stale else None
    return JSONResponse(content=bars, headers=headers)


def _create_redis() -> aioredis.Redis:
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    db = int(os.environ.get("REDIS_DB", "0"))
    return aioredis.Redis(host=host, port=port, db=db, decode_responses=True)


def create_app(start_loops: bool = True) -> FastAPI:
    """组装桥应用；start_loops=False 供测试只验 HTTP 面"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        redis_conn = _create_redis()
        state = bridge.BridgeState()
        kline_guard = KlineGuard()
        _app.state.redis = redis_conn
        _app.state.sina_state = state
        _app.state.kline_guard = kline_guard
        tasks: list[asyncio.Task] = []
        if start_loops:
            tasks.append(
                asyncio.create_task(
                    bridge.quote_poll_loop(redis_conn, state), name="sina-quote-poll"
                )
            )
            tasks.append(
                asyncio.create_task(
                    bridge.monthly_fetch_loop(redis_conn, state),
                    name="sina-monthly",
                )
            )
            tasks.append(
                asyncio.create_task(
                    bridge.heartbeat_loop(redis_conn, state), name="sina-heartbeat"
                )
            )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await bridge.close_replica()
            await kline_guard.aclose()
            await redis_conn.aclose()

    app = FastAPI(
        title="qihuo-sina-bridge", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        redis_ok = False
        try:
            redis_ok = bool(await app.state.redis.ping())
        except Exception:
            logger.warning("healthcheck：Redis ping 失败", exc_info=True)
        state: bridge.BridgeState = app.state.sina_state
        now_age = None
        if state.last_poll_ts:
            now_age = round(time.time() - state.last_poll_ts, 1)
        ok = redis_ok
        payload = {
            "ok": ok,
            "redis": redis_ok,
            "interval_mode": state.interval_mode,
            "last_poll_ok": state.last_poll_ok,
            "last_poll_age": now_age,
            "replica_enabled": state.replica_enabled,
            "last_replica_ok": state.last_replica_ok,
            "last_replica_error": state.last_replica_error,
        }
        if not ok:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        payload = app.state.sina_state.snapshot()
        payload.update(app.state.kline_guard.snapshot())
        return payload

    @app.get("/quotes")
    async def quotes() -> list[dict[str, Any]]:
        """最近一轮成功轮询的缓存快照（≤ 轮询间隔年龄）"""
        return app.state.sina_state.latest_quotes

    @app.get("/quote/{symbol}")
    async def quote(symbol: str) -> dict[str, Any]:
        """按需拉取单合约实时行情（AI 工具 Redis miss 兜底用）"""
        from app.data.sina_quote import fetch_sina_quote

        result = await asyncio.to_thread(fetch_sina_quote, symbol)
        if not result:
            raise HTTPException(status_code=404, detail=f"sina 无此合约行情: {symbol}")
        return result

    @app.get("/kline/minute")
    async def kline_minute(
        symbol: str = Query(..., min_length=1),
        period: str = Query("1m", pattern="^(1m|5m|15m|30m|60m)$"),
        limit: int = Query(0, ge=0),
    ) -> Response:
        bars, stale = await _guarded_kline(app, "minute", symbol, period)
        if limit:
            bars = bars[-limit:]
        return _bars_response(bars, stale)

    @app.get("/kline/daily")
    async def kline_daily(
        symbol: str = Query(..., min_length=1),
        limit: int = Query(0, ge=0),
    ) -> Response:
        bars, stale = await _guarded_kline(app, "daily", symbol, "")
        if limit:
            bars = bars[-limit:]
        return _bars_response(bars, stale)

    @app.get("/monthly")
    async def monthly_fetch() -> dict[str, int]:
        """手动触发一次月度全合约拉取（运维用；常规由后台循环覆盖）"""
        count = await bridge.monthly_fetch_once(app.state.redis, app.state.sina_state)
        app.state.sina_state.last_monthly_ts = time.time()
        app.state.sina_state.last_monthly_count = count
        return {"count": count}

    return app
