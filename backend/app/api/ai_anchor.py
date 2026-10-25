"""AI 看盘主播 API —— 配置启动/暂停/恢复/停止与播报查询（按 JWT trading_mode 分盘）"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import AuthContext, get_auth_context, get_session
from app.schemas.ai_anchor import (
    AnchorBroadcastOut,
    AnchorConfigPatchIn,
    AnchorPositionIn,
    AnchorStartIn,
    AnchorStatusOut,
    AnchorTaskOut,
)
from app.services.ai_anchor import svc
from app.services.ai_anchor.engine import anchor_market_open
from app.services.ai_anchor.indicators import INDICATOR_PARAM_SCHEMA
from app.services.ai_anchor.tts import CLOUD_VOICES, synthesize
from app.services.factor_lab.llm.llm_http import load_user_model

router = APIRouter(prefix="/api/ai-anchor", tags=["AI看盘主播"])


def _task_out(task, market_open: bool) -> AnchorTaskOut:
    """ORM 任务转响应模型。"""
    return AnchorTaskOut(
        id=task.id,
        model_row_id=task.model_row_id,
        symbol=task.symbol,
        timeframe=task.timeframe,
        timeframes=svc.task_timeframes(task),
        direction_mode=str(getattr(task, "direction_mode", None) or "any"),
        strategy=str(getattr(task, "strategy", None) or "balanced"),
        horizon=str(getattr(task, "horizon", None) or "short"),
        bar_count=task.bar_count,
        interval_minutes=task.interval_minutes,
        indicators=list(task.indicators or []),
        status=task.status,
        next_run_at=task.next_run_at,
        last_run_at=task.last_run_at,
        market_open=market_open,
        manual_position=getattr(task, "manual_position", None) or None,
    )


def _broadcast_out(row) -> AnchorBroadcastOut:
    """ORM 播报转响应模型（持仓建议存于 model_output JSONB）。"""
    model_output = getattr(row, "model_output", None) or {}
    advice = model_output.get("position_advice") if isinstance(model_output, dict) else None
    return AnchorBroadcastOut(
        id=row.id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        direction=row.direction,
        entry=row.entry,
        take_profit=row.take_profit,
        stop_loss=row.stop_loss,
        action=row.action,
        commentary=row.commentary,
        model_error=row.model_error,
        key_levels=list(getattr(row, "key_levels", None) or []),
        position_advice=advice if isinstance(advice, dict) else None,
        bar_time=row.bar_time,
        created_at=row.created_at,
    )


async def _require_task(
    session: AsyncSession,
    ctx: AuthContext,
) -> object:
    """取当前用户的主播任务；不存在则 404。"""
    task = await svc.get_task(session, ctx.user.id, ctx.trading_mode)
    if task is None:
        raise HTTPException(status_code=404, detail="尚未创建主播任务")
    return task


@router.get("/indicators")
async def list_indicators(
    ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    """可选技术线及参数 schema（供前端渲染参数表单）。"""
    return {"indicators": INDICATOR_PARAM_SCHEMA}


class AnchorTtsIn(BaseModel):
    """云端 TTS 合成请求"""

    text: Annotated[str, Field(min_length=1, max_length=500, description="合成文本")]
    voice: Annotated[str, Field(min_length=1, max_length=64, description="音色 ID")]
    rate: Annotated[float, Field(default=1.0, ge=0.5, le=1.5, description="语速 0.5-1.5")]


@router.get("/voices")
async def list_voices(
    ctx: AuthContext = Depends(get_auth_context),
) -> dict:
    """云端自然音色列表（微软 Edge 神经网络音色,后端代理合成）。"""
    return {"voices": CLOUD_VOICES}


@router.post("/tts")
async def synthesize_tts(
    body: AnchorTtsIn,
    ctx: AuthContext = Depends(get_auth_context),
) -> Response:
    """云端 TTS 合成,返回 mp3 音频;失败 502 由前端回退本地语音。"""
    try:
        audio = await synthesize(body.text, body.voice, body.rate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg")


@router.get("/status", response_model=AnchorStatusOut)
async def get_status(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """主播状态总览：任务配置/状态 + 最近播报。"""
    task = await svc.get_task(session, ctx.user.id, ctx.trading_mode)
    if task is None:
        return AnchorStatusOut(task=None, recent_broadcasts=[])
    broadcasts = await svc.list_broadcasts(
        session, ctx.user.id, task.id, svc.RECENT_BROADCASTS
    )
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[_broadcast_out(row) for row in broadcasts],
    )


@router.post("/start", response_model=AnchorStatusOut)
async def start_anchor(
    body: AnchorStartIn,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """保存配置并启动主播（每用户每盘模式单任务，覆盖配置）。"""
    loaded = await load_user_model(session, ctx.user.id, body.model_row_id)
    if loaded is None:
        raise HTTPException(status_code=400, detail="模型不存在或不属于当前用户")
    indicators = svc.normalize_indicators(
        [item.model_dump() for item in body.indicators]
    )
    for item in indicators:
        if item["name"] not in INDICATOR_PARAM_SCHEMA:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的技术线: {item['name']}，可选: {', '.join(INDICATOR_PARAM_SCHEMA)}",
            )
    task = await svc.upsert_task(
        session,
        ctx.user.id,
        ctx.trading_mode,
        body.model_row_id,
        body.symbol.strip().lower(),
        body.effective_timeframes,
        body.direction_mode,
        body.bar_count,
        body.interval_minutes,
        indicators,
        strategy=body.strategy,
        horizon=body.horizon,
    )
    await session.commit()
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[],
    )


@router.post("/pause", response_model=AnchorStatusOut)
async def pause_anchor(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """暂停主播（保留配置与播报历史）。"""
    task = await _require_task(session, ctx)
    await svc.set_task_status(session, task, "paused")
    await session.commit()
    broadcasts = await svc.list_broadcasts(
        session, ctx.user.id, task.id, svc.RECENT_BROADCASTS
    )
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[_broadcast_out(row) for row in broadcasts],
    )


@router.post("/resume", response_model=AnchorStatusOut)
async def resume_anchor(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """恢复主播（立即到期触发一轮播报）。"""
    task = await _require_task(session, ctx)
    await svc.set_task_status(session, task, "running")
    await session.commit()
    broadcasts = await svc.list_broadcasts(
        session, ctx.user.id, task.id, svc.RECENT_BROADCASTS
    )
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[_broadcast_out(row) for row in broadcasts],
    )


@router.post("/stop", response_model=AnchorStatusOut)
async def stop_anchor(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """停止主播（配置保留为草稿，播报历史保留）。"""
    task = await _require_task(session, ctx)
    await svc.set_task_status(session, task, "stopped")
    await session.commit()
    broadcasts = await svc.list_broadcasts(
        session, ctx.user.id, task.id, svc.RECENT_BROADCASTS
    )
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[_broadcast_out(row) for row in broadcasts],
    )


@router.patch("/config", response_model=AnchorStatusOut)
async def patch_config(
    body: AnchorConfigPatchIn,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """运行中热更新配置（切换模型/操盘策略/交易风格）。

    只改请求给出的字段，不动任务状态与下次播报时间——
    模型/策略从下一轮播报自然生效。
    """
    task = await _require_task(session, ctx)
    if body.model_row_id is not None:
        loaded = await load_user_model(session, ctx.user.id, body.model_row_id)
        if loaded is None:
            raise HTTPException(status_code=400, detail="模型不存在或不属于当前用户")
        task.model_row_id = body.model_row_id
    if body.strategy is not None:
        task.strategy = body.strategy
    if body.horizon is not None:
        task.horizon = body.horizon
    await session.commit()
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[],
    )


@router.put("/position", response_model=AnchorStatusOut)
async def set_position(
    body: AnchorPositionIn,
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """录入/更新手动持仓：下一轮播报起汇报持仓状态并给持仓操作建议。"""
    task = await _require_task(session, ctx)
    task.manual_position = {
        "symbol": body.symbol.strip().lower(),
        "direction": body.direction,
        "price": round(body.price, 4),
        "quantity": body.quantity,
    }
    await session.commit()
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[],
    )


@router.delete("/position", response_model=AnchorStatusOut)
async def clear_position(
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> AnchorStatusOut:
    """清除手动持仓：恢复普通播报（无持仓状态汇报）。"""
    task = await _require_task(session, ctx)
    task.manual_position = None
    await session.commit()
    return AnchorStatusOut(
        task=_task_out(task, anchor_market_open(task)),
        recent_broadcasts=[],
    )


@router.get("/broadcasts")
async def list_broadcasts(
    limit: int = Query(default=50, ge=1, le=200, description="返回条数"),
    ctx: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """播报历史（倒序）。"""
    task = await _require_task(session, ctx)
    rows = await svc.list_broadcasts(session, ctx.user.id, task.id, limit)
    return {"total": len(rows), "items": [_broadcast_out(row).model_dump() for row in rows]}
