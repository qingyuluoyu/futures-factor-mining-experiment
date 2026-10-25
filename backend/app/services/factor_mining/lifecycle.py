"""超级因子挖掘 —— 任务状态机服务（创建/暂停/恢复/取消/查询）"""

from __future__ import annotations

import logging
from datetime import datetime, timezone


def _now() -> datetime:
    """带时区的 UTC now（task 时间列是 timezone-aware，必须用 aware datetime）"""
    return datetime.now(timezone.utc)
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.factor_lab.common import champion_payload
from app.models.factor_mining.task import FactorMiningTask
from app.services.factor_mining.loader import load_mining_bars

logger = logging.getLogger("qihuo.factor_mining")

# 单用户同时运行的任务数上限（防止资源滥用）
MAX_RUNNING_PER_USER = 2


async def create_task(
    session: AsyncSession,
    user_id: UUID,
    *,
    symbol: str,
    timeframe: str = "1d",
    population: int = 40,
    generations: int = 30,
    max_depth: int = 4,
    train_ratio: float = 0.7,
    walk_forward_folds: int = 3,
    live_fill_gate: bool = False,
    cross_validate: bool = False,
    islands: int = 1,
    seed_tokens: list[list[int]] | None = None,
    device: str = "cpu",
    name: str = "",
    redis_conn: object | None = None,
) -> dict[str, Any]:
    """创建挖掘任务：校验配额、预加载数据范围、写库后返回任务详情。

    数据范围在创建时即加载确认（fail-fast：若 PG 无日线历史，这里就报错，
    不让无效任务进入 running）。
    """
    # 配额校验：限制单用户同时运行数
    stmt = select(FactorMiningTask).where(
        FactorMiningTask.user_id == user_id,
        FactorMiningTask.status.in_(("pending", "running")),
    )
    running = (await session.execute(stmt)).scalars().all()
    if len(running) >= MAX_RUNNING_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"每个用户最多同时 {MAX_RUNNING_PER_USER} 个运行中任务",
        )

    # 预加载数据范围（fail-fast 校验数据可用性）
    bars, range_from, range_to = await load_mining_bars(
        redis_conn, session, symbol, timeframe
    )

    if device == "gpu":
        raise HTTPException(
            status_code=400,
            detail="GPU 挖掘尚未实现（MVP 仅支持 CPU）",
        )

    # symbol 规整：用户选的是品种 code（如 rb），转成主力合约符号（如 rb2610），
    # 与因子实验室/AI交易/回测统一口径（它们都需要具体合约符号 rb2610）。
    # 这样收藏后到回测/AI交易里能正确找到品种。
    from app.data.contracts import get_master_symbol_for_code

    master_symbol = get_master_symbol_for_code(symbol.strip().lower())

    task = FactorMiningTask(
        user_id=user_id,
        name=name or f"超挖·{symbol}·{timeframe}",
        symbol=master_symbol,
        timeframe=timeframe,
        population=population,
        generations=generations,
        max_depth=max_depth,
        train_ratio=train_ratio,
        walk_forward_folds=walk_forward_folds,
        live_fill_gate=live_fill_gate,
        cross_validate=cross_validate,
        islands=islands,
        seed_tokens=seed_tokens or [],
        device=device,
        bars_count=len(bars),
        data_range_from=range_from,
        data_range_to=range_to,
        status="pending",
    )
    session.add(task)
    await session.flush()
    await session.commit()
    await session.refresh(task)
    logger.info(
        "创建挖掘任务 %s: %s %s %d根 %d代",
        task.id, symbol, timeframe, len(bars), generations,
    )
    return task_to_dict(task)


async def list_tasks(
    session: AsyncSession,
    user_id: UUID,
) -> list[dict[str, Any]]:
    """列出用户的所有挖掘任务（按创建时间倒序）"""
    stmt = (
        select(FactorMiningTask)
        .where(FactorMiningTask.user_id == user_id)
        .order_by(FactorMiningTask.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [task_to_dict(t) for t in rows]


async def get_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> dict[str, Any]:
    """查询单个任务详情"""
    task = await _get_owned(session, user_id, task_id)
    return task_to_dict(task)


async def pause_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> dict[str, Any]:
    """用户手动暂停（loop 下个代检查到 status 变化即停止推进）"""
    task = await _get_owned(session, user_id, task_id)
    if task.status not in ("running", "pending"):
        raise HTTPException(status_code=400, detail="仅运行中/待运行任务可暂停")
    task.status = "paused"
    task.pause_reason = "user"
    task.running_worker = None
    task.updated_at = _now()
    await session.commit()
    await session.refresh(task)
    return task_to_dict(task)


async def resume_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> dict[str, Any]:
    """恢复暂停的任务（loop 下一轮从 current_generation 续跑）"""
    task = await _get_owned(session, user_id, task_id)
    if task.status != "paused":
        raise HTTPException(status_code=400, detail="仅暂停任务可恢复")
    # 配额校验
    stmt = select(FactorMiningTask).where(
        FactorMiningTask.user_id == user_id,
        FactorMiningTask.status.in_(("pending", "running")),
    )
    running = (await session.execute(stmt)).scalars().all()
    if len(running) >= MAX_RUNNING_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"每个用户最多同时 {MAX_RUNNING_PER_USER} 个运行中任务",
        )
    task.status = "pending"
    task.pause_reason = None
    task.updated_at = _now()
    await session.commit()
    await session.refresh(task)
    return task_to_dict(task)


async def cancel_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> dict[str, Any]:
    """取消任务（loop 检测到 cancelled 即停止）"""
    task = await _get_owned(session, user_id, task_id)
    if task.status in ("completed", "cancelled"):
        raise HTTPException(status_code=400, detail="任务已结束")
    task.status = "cancelled"
    task.running_worker = None
    task.completed_at = _now()
    task.updated_at = _now()
    await session.commit()
    await session.refresh(task)
    return task_to_dict(task)


async def get_champions(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> list[dict[str, Any]]:
    """获取完成任务的冠军因子（用 champion_payload 序列化，含 train/test 双指标）"""
    task = await _get_owned(session, user_id, task_id)
    if task.status != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")
    # champions 存的是 Champion dataclass 的 dict 化结果，复用 champion_payload
    return champion_payload(task.champions or [])


async def delete_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> None:
    """删除任务"""
    task = await _get_owned(session, user_id, task_id)
    await session.delete(task)
    await session.commit()


async def _get_owned(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
) -> FactorMiningTask:
    stmt = select(FactorMiningTask).where(
        FactorMiningTask.id == task_id,
        FactorMiningTask.user_id == user_id,
    )
    task = (await session.execute(stmt)).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def task_to_dict(task: FactorMiningTask) -> dict[str, Any]:
    """任务 ORM → API dict（不含 champions 详情，列表/详情通用）"""
    return {
        "id": str(task.id),
        "name": task.name,
        "symbol": task.symbol,
        "timeframe": task.timeframe,
        "population": task.population,
        "generations": task.generations,
        "max_depth": task.max_depth,
        "train_ratio": float(task.train_ratio),
        "walk_forward_folds": task.walk_forward_folds,
        "device": task.device,
        "bars_count": task.bars_count,
        "data_range_from": task.data_range_from,
        "data_range_to": task.data_range_to,
        "status": task.status,
        "pause_reason": task.pause_reason,
        "current_generation": task.current_generation,
        "best_composite": float(task.best_composite),
        "progress_pct": task.progress_pct,
        "champions_count": len(task.champions or []),
        "error_msg": task.error_msg,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }
