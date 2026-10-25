"""AI 看盘主播任务服务 —— 任务 upsert / 查询 / 播报历史"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_anchor import AIAnchorBroadcast, AIAnchorTask
from app.services.ai_anchor.indicators import normalize_params

# 注入 prompt 的历史播报条数（上下文连贯）
_CONTEXT_BROADCASTS = 5
# 状态接口默认返回的最近播报条数
RECENT_BROADCASTS = 20
# 合法分时K线段
VALID_TIMEFRAMES = ("1m", "5m", "15m", "30m", "60m")
# 合法交易风格
VALID_HORIZONS = ("short", "mid", "long")


def normalize_timeframes(items: Any) -> list[str]:
    """归一分时段列表：白名单过滤 + 保序去重 + 上限3个（至少回退单周期）。"""
    raw = items if isinstance(items, (list, tuple)) else []
    seen: set[str] = set()
    out: list[str] = []
    for tf in raw:
        value = str(tf)
        if value in VALID_TIMEFRAMES and value not in seen:
            seen.add(value)
            out.append(value)
    return out[:3]


def task_timeframes(task: AIAnchorTask) -> list[str]:
    """任务的有效分时段列表：timeframes 列表优先，回退单周期列。"""
    normalized = normalize_timeframes(getattr(task, "timeframes", None))
    if normalized:
        return normalized
    return [str(task.timeframe)]

# 上下文每日重置时刻：北京时间 20:00（夜盘 21:00 开盘前清空，
# 每个交易"战斗日"全新开局）；周六/周日不执行清空
_BJ_TZ = timezone(timedelta(hours=8))
_CONTEXT_RESET_HOUR = 20


def utc_now() -> datetime:
    """当前 UTC 时间（带时区）。"""
    return datetime.now(timezone.utc)


def context_boundary_utc(now_utc: datetime) -> datetime:
    """主播上下文时间起点：最近一个非周六/周日 的北京时间 20:00（转 UTC）。

    周六/周日不执行清空：周六凌晨（夜盘尾巴）、周末及周一白天取到的
    边界都是上周五 20:00，跨周末的观点得以保留到周一 20:00 才重置。
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_bj = now_utc.astimezone(_BJ_TZ)
    day = now_bj.date()
    if now_bj.hour >= _CONTEXT_RESET_HOUR and day.weekday() < 5:
        boundary = datetime.combine(day, time(_CONTEXT_RESET_HOUR), _BJ_TZ)
    else:
        day = day - timedelta(days=1)
        while day.weekday() >= 5:  # 5=周六 6=周日
            day = day - timedelta(days=1)
        boundary = datetime.combine(day, time(_CONTEXT_RESET_HOUR), _BJ_TZ)
    return boundary.astimezone(timezone.utc)


async def get_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    trading_mode: str,
) -> AIAnchorTask | None:
    """取当前用户（按盘模式）的主播任务；每用户每模式仅一条。"""
    stmt = select(AIAnchorTask).where(
        AIAnchorTask.user_id == user_id,
        AIAnchorTask.trading_mode == trading_mode,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def normalize_indicators(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把请求中的技术线组合归一化（参数补默认值），保持顺序与重复项。"""
    result: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip().upper()
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        result.append({"name": name, "params": normalize_params(name, params)})
    return result


async def upsert_task(
    session: AsyncSession,
    user_id: uuid.UUID,
    trading_mode: str,
    model_row_id: uuid.UUID,
    symbol: str,
    timeframes: list[str],
    direction_mode: str,
    bar_count: int,
    interval_minutes: int,
    indicators: list[dict[str, Any]],
    strategy: str = "balanced",
    horizon: str = "short",
) -> AIAnchorTask:
    """保存/更新主播配置并置为运行中（每用户每模式覆盖单条）。"""
    task = await get_task(session, user_id, trading_mode)
    now = utc_now()
    mode = direction_mode if direction_mode in ("any", "long", "short") else "any"
    sty = strategy if strategy in ("aggressive", "balanced", "conservative") else "balanced"
    hrz = horizon if horizon in VALID_HORIZONS else "short"
    normalized_tfs = normalize_timeframes(timeframes)
    primary_tf = normalized_tfs[0] if normalized_tfs else "15m"
    if task is None:
        task = AIAnchorTask(
            user_id=user_id,
            trading_mode=trading_mode,
            model_row_id=model_row_id,
            symbol=symbol,
            timeframe=primary_tf,
            timeframes=normalized_tfs or [primary_tf],
            direction_mode=mode,
            strategy=sty,
            horizon=hrz,
            bar_count=bar_count,
            interval_minutes=interval_minutes,
            indicators=indicators,
        )
        session.add(task)
    else:
        task.model_row_id = model_row_id
        task.symbol = symbol
        task.timeframe = primary_tf
        task.timeframes = normalized_tfs or [primary_tf]
        task.direction_mode = mode
        task.strategy = sty
        task.horizon = hrz
        task.bar_count = bar_count
        task.interval_minutes = interval_minutes
        task.indicators = indicators
    # 启动即置运行中，立即到期待首轮播报
    task.status = "running"
    task.next_run_at = now
    await session.flush()
    return task


async def set_task_status(
    session: AsyncSession,
    task: AIAnchorTask,
    status: str,
) -> None:
    """更新任务状态；恢复运行时立即到期触发播报。"""
    task.status = status
    if status == "running":
        task.next_run_at = utc_now()
    await session.flush()


async def list_broadcasts(
    session: AsyncSession,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    limit: int,
) -> list[AIAnchorBroadcast]:
    """按时间倒序取播报历史（校验任务归属用户）。"""
    stmt = (
        select(AIAnchorBroadcast)
        .where(
            AIAnchorBroadcast.user_id == user_id,
            AIAnchorBroadcast.task_id == task_id,
        )
        .order_by(desc(AIAnchorBroadcast.created_at), desc(AIAnchorBroadcast.id))
        .limit(max(1, min(limit, 200)))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_broadcasts_by_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    limit: int,
) -> list[AIAnchorBroadcast]:
    """按任务取播报历史（供引擎/循环内部使用）。"""
    stmt = (
        select(AIAnchorBroadcast)
        .where(AIAnchorBroadcast.task_id == task_id)
        .order_by(desc(AIAnchorBroadcast.created_at), desc(AIAnchorBroadcast.id))
        .limit(max(1, min(limit, 200)))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def build_context_history(
    session: AsyncSession,
    task: AIAnchorTask,
) -> list[dict[str, Any]]:
    """取注入 prompt 的历史播报摘要：同任务最近几条成功播报。

    错误播报不含有效观点，排除在上下文之外；
    品种与当前配置一致才纳入（换品种后旧观点不参与）；
    不再按 timeframe 过滤——多时段综合播报/调整时段组合后观点保持连贯；
    只取最近一个非周末北京时间 20:00 之后的播报（每日上下文重置）。
    """
    stmt = (
        select(AIAnchorBroadcast)
        .where(
            AIAnchorBroadcast.task_id == task.id,
            AIAnchorBroadcast.model_error.is_(False),
            AIAnchorBroadcast.symbol == task.symbol,
            AIAnchorBroadcast.created_at >= context_boundary_utc(utc_now()),
        )
        .order_by(desc(AIAnchorBroadcast.created_at), desc(AIAnchorBroadcast.id))
        .limit(_CONTEXT_BROADCASTS)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    # 时间正序喂给模型（越新越靠后）
    rows.reverse()
    return [
        {
            "time": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
            "direction": row.direction,
            "action": row.action,
            "entry": row.entry,
            "take_profit": row.take_profit,
            "stop_loss": row.stop_loss,
            "commentary": row.commentary,
        }
        for row in rows
    ]
