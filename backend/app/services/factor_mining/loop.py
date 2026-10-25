"""超级因子挖掘后台循环 —— 由 scheduler 选主 worker 启动

与 ai_trading_loop 同范式：while True 轮询 pending/running 任务，分代步进推进。
区别：因子挖掘是 CPU 密集，每轮只推进一代（用 asyncio.to_thread 跑同步搜索），
并把进度写回 DB，支持暂停/取消/断点续训。
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timezone


def _now() -> datetime:
    """带时区的 UTC now。task 时间列是 DateTime(timezone=True)，
    必须用 aware datetime 比较/赋值，否则与 DB 读出的 aware 值相减会抛
    TypeError: can't subtract offset-naive and offset-aware datetimes。"""
    return datetime.now(timezone.utc)
from typing import Any

from sqlalchemy import select

from app.models.factor_mining.task import FactorMiningTask
from app.services.factor_lab.scoring.cost import (
    DEFAULT_SLIPPAGE_TICKS,
    turnover_cost_rate,
)
from app.services.factor_lab.search import SearchConfig, search_stepwise
from app.services.factor_mining.loader import load_mining_bars
from app.services.factor_mining.capabilities import checkpoint_worker_connected
from app.services.trading_hours import is_trading_hours

logger = logging.getLogger("qihuo.factor_mining")

# 循环间隔：有任务时快速推进，无任务时降频
_INTERVAL_ACTIVE = 1.0
_INTERVAL_IDLE = 15.0
# 单次推进的代数：超长数据（几千根 bars）单代计算已较重，每 tick 只跑 1 代，
# 避免单次 to_thread 长时间占用线程池导致事件循环饥饿、任务卡死。
_STEPS_PER_TICK = 1
# 单步超时（秒）：单次推进超过此时长视为卡死，重置 worker + 暂停任务。
# 超长数据下单代正常约 1-2 分钟，给 5 分钟余量。
_STEP_TIMEOUT = 300
# 卡死自愈：running 但 updated_at 超过此时长（秒）的任务，下轮自动重置。
_STALL_THRESHOLD = 600

_loop_task: asyncio.Task | None = None
# 本 worker 唯一标识（用于 running_worker 占位，防多 worker 重复执行）
_WORKER_ID = f"{socket.gethostname()}-{id(socket)}"


async def factor_mining_loop(
    session_factory: Any,
    redis_conn: Any,
) -> None:
    """后台循环主体

    非交易时段（周末/节假日/休市）：暂停推进、不拉取 K 线数据，
    仅低频检查是否开盘 —— 避免周末 1s 高频请求触发数据源 IP 封禁
    （历史回顾：周六因子挖掘循环每秒拉取导致新浪接口被风控封禁）。
    """
    logger.info("超级因子挖掘循环已启动（非交易时段暂停推进，开盘自动恢复）")
    while True:
        if not is_trading_hours():
            await asyncio.sleep(_INTERVAL_IDLE)
            continue
        try:
            n = await _process_pending_and_running(session_factory, redis_conn)
            interval = _INTERVAL_ACTIVE if n else _INTERVAL_IDLE
        except Exception:
            logger.exception("因子挖掘批处理失败")
            interval = _INTERVAL_IDLE
        await asyncio.sleep(interval)


async def _process_pending_and_running(
    session_factory: Any,
    redis_conn: Any,
) -> int:
    """推进所有 pending/running 任务，每个最多 _STEPS_PER_TICK 代"""
    if session_factory is None:
        return 0
    if not checkpoint_worker_connected():
        return 0
    processed = 0
    async with session_factory() as session:
        # 先做卡死自愈：running 但 updated_at 超阈值的任务，重置为 paused
        await _reclaim_stalled(session)
        # 取所有 pending/running 任务（pending 是刚创建/刚恢复的）
        stmt = select(FactorMiningTask).where(
            FactorMiningTask.status.in_(("pending", "running"))
        )
        tasks = list((await session.execute(stmt)).scalars().all())
        for task in tasks:
            # 占位检查：已被其他 worker 认领且心跳未过期则跳过（防多 worker 重复执行）
            if task.status == "running" and task.running_worker and task.running_worker != _WORKER_ID:
                continue
            try:
                # 单步超时保护：防止超长数据上单代计算无限占用线程
                await asyncio.wait_for(
                    _advance_task(session, redis_conn, task, _STEPS_PER_TICK),
                    timeout=_STEP_TIMEOUT,
                )
                processed += 1
            except asyncio.TimeoutError:
                # 注意：to_thread 的同步计算无法被强制中断（Python 线程限制），
                # 超时后底层线程仍在跑直至完成或进程重启。这里把任务标 paused，
                # 让用户知道卡住了、可手动恢复；避免任务永久卡在 running。
                logger.warning("任务 %s 单步超时(>%ds)，重置为暂停", task.id, _STEP_TIMEOUT)
                await _reclaim_one(session, task, f"单步计算超时(>{_STEP_TIMEOUT}s)")
            except Exception as exc:
                logger.exception("推进任务 %s 失败", task.id)
                await _mark_failed(session, task, str(exc))
        await session.commit()
    return processed


async def _reclaim_stalled(session: Any) -> None:
    """卡死自愈：running 但 updated_at 超过 _STALL_THRESHOLD 的任务，重置为 paused。

    场景：worker 进程崩溃/重启后，旧任务的 running_worker 残留、status 还是 running，
    但实际上没人推进。每轮循环开头清理这类任务，避免它们永远卡在 running。
    """
    now = _now()
    stmt = select(FactorMiningTask).where(
        FactorMiningTask.status == "running",
        FactorMiningTask.running_worker.isnot(None),
    )
    for task in (await session.execute(stmt)).scalars().all():
        updated = task.updated_at
        if updated and (now - updated).total_seconds() > _STALL_THRESHOLD:
            await _reclaim_one(session, task, f"运行超过 {_STALL_THRESHOLD}s 无更新，疑似卡死")


async def _reclaim_one(session: Any, task: FactorMiningTask, reason: str) -> None:
    """把单个任务重置为 paused（清 worker 占位），可被用户手动恢复续跑"""
    task.status = "paused"
    task.pause_reason = "stalled"
    task.running_worker = None
    task.error_msg = reason[:500]
    task.updated_at = _now()
    logger.info("任务 %s 已重置为暂停：%s", task.id, reason)


async def _advance_task(
    session: Any,
    redis_conn: Any,
    task: FactorMiningTask,
    max_steps: int,
) -> None:
    """推进单个任务最多 max_steps 代"""
    # 认领任务
    task.status = "running"
    task.running_worker = _WORKER_ID
    if task.started_at is None:
        task.started_at = _now()

    # 加载数据（每个任务的数据范围在创建时已固化，这里按 symbol/timeframe 重新加载）
    bars, _from, _to = await load_mining_bars(
        redis_conn, session, task.symbol, task.timeframe
    )
    cost = _resolve_cost(task.symbol, bars)
    # 跨品种验证伙伴：异步预加载（日线主库；分钟较重暂跳过）
    cross_peers = None
    if getattr(task, "cross_validate", False) and task.timeframe == "1d":
        try:
            from app.services.factor_lab.cross_symbol import load_cross_peer_bars

            cross_peers = await load_cross_peer_bars(
                session, task.symbol, task.timeframe, max_bars=len(bars)
            )
        except Exception:
            logger.exception("跨品种伙伴加载失败（该门跳过）")
            cross_peers = None
    cfg = SearchConfig(
        population=task.population,
        generations=task.generations,
        max_depth=task.max_depth,
        cost=cost,
        train_ratio=task.train_ratio,
        walk_forward_folds=task.walk_forward_folds,
        live_fill_gate=getattr(task, "live_fill_gate", False),
        cross_peers=cross_peers,
        islands=getattr(task, "islands", 1) or 1,
        seed_tokens=task.seed_tokens or None,
    )

    # 断点续训：从 current_generation 续跑，注入历史最优（从 champions 字段恢复）
    seed_best = _recover_seed_best(task)

    def run_steps() -> list:
        """同步执行最多 max_steps 代，返回每代的 snapshot"""
        snapshots = []
        gen = search_stepwise(
            bars, task.timeframe, cfg,
            start_generation=task.current_generation,
            seed_best=seed_best,
        )
        steps = 0
        for snap in gen:
            snapshots.append(snap)
            steps += 1
            if steps >= max_steps:
                break
        return snapshots

    # 同步搜索放线程池，避免阻塞事件循环
    snapshots = await asyncio.to_thread(run_steps)
    if not snapshots:
        # 无新代可跑（任务恢复时已到末代）→ 用已持久化的 task.champions 完成
        from app.services.factor_lab.search import Champion

        persisted = [
            Champion(
                tokens=list(c.get("tokens") or []),
                text=str(c.get("text") or ""),
                metrics=c.get("metrics") if isinstance(c.get("metrics"), dict) else {},
                composite=float(c.get("composite") or 0.0),
            )
            for c in (task.champions or [])
            if isinstance(c, dict) and c.get("tokens")
        ]
        await _mark_completed(session, task, persisted, bars, task.timeframe, cost, cfg)
        return

    # 更新进度到最新 snapshot
    last = snapshots[-1]
    task.current_generation = last.generation
    task.best_composite = last.best_composite
    task.progress_pct = int(last.generation * 100 / max(1, last.total_generations))
    # 关键：把每代的 champion 快照持久化到 task.champions。
    # 否则 _mark_completed 用空的 seed_best 去重，会得到 0 冠军。
    # （_recover_seed_best 从 task.champions 恢复，断点续训也依赖此）
    if last.champions:
        task.champions = _champions_to_dicts(last.champions)
    task.updated_at = _now()

    # 若已跑完所有代 → 标记完成。直接用最后 snapshot 的 champions（已含防过拟合筛选）
    # 作为最终结果，无需再用 seed_best 重跑 _dedup_top。
    if last.generation >= task.generations:
        await _mark_completed(session, task, last.champions, bars, task.timeframe, cost, cfg)


def _recover_seed_best(task: FactorMiningTask) -> list[tuple[float, list[int], dict]]:
    """从任务已存储的 champions 恢复 seed_best（断点续训用）

    champions 字段存的是 champion_payload 的输出（dict 列表），这里还原成
    search_stepwise 期望的 (composite, tokens, metrics) 三元组。
    """
    out: list[tuple[float, list[int], dict]] = []
    for c in (task.champions or []):
        if isinstance(c, dict):
            tokens = list(c.get("tokens") or [])
            composite = float(c.get("composite") or 0.0)
            metrics = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
            if tokens:
                out.append((composite, tokens, metrics))
    return out


async def _mark_completed(
    session: Any,
    task: FactorMiningTask,
    final_champions: list,
    bars: list,
    timeframe: str,
    cost: float,
    cfg: SearchConfig,
) -> None:
    """任务完成：固化最终 champions（已含防过拟合筛选），写入因子历史。

    final_champions 是 search_stepwise 最后一代 snapshot 的 champions（Champion 对象），
    已经过 _dedup_top 的两轮筛选（严格筛 + 兜底回退），直接 dict 化存入即可，
    无需再用 seed_best 重跑 _dedup_top（之前那么做会因 seed_best 为空得到 0 冠军）。
    """
    task.champions = _champions_to_dicts(final_champions)
    task.status = "completed"
    task.progress_pct = 100
    task.current_generation = task.generations
    task.running_worker = None
    task.completed_at = _now()
    task.updated_at = _now()
    logger.info("挖掘任务完成 %s: %d 个冠军因子", task.id, len(task.champions))

    # 把冠军因子写入因子历史（复用 factor_lab 的 FactorHistory 表 + FIFO）
    # 这样超级因子挖出的因子在因子实验室的历史面板也能看到，两个页面因子库打通
    try:
        from app.api.factor_lab.common import champion_payload
        from app.services.factor_lab.persist.history_svc import (
            upsert_champions_fifo,
        )

        payload = champion_payload(final_champions, stamp_kernel=True)
        await upsert_champions_fifo(
            session,
            task.user_id,
            task.symbol,
            task.timeframe,
            payload,
            "mining",  # source=mining 标记来自超级因子挖掘
        )
        await session.flush()
        logger.info(
            "挖掘任务 %s 冠军因子已写入因子历史(source=mining)", task.id
        )
    except Exception:
        # 历史写入失败不影响任务完成结果
        logger.exception("写入因子历史失败（不影响任务完成）")

    # 试验登记（多重检验修正原料，best-effort）
    try:
        from app.api.factor_lab.common import champion_payload
        from app.services.factor_lab.persist.trial_svc import log_search_trial

        await log_search_trial(
            session,
            user_id=task.user_id,
            source="mining",
            symbol=task.symbol,
            timeframe=task.timeframe,
            trials=cfg.population * cfg.generations + len(cfg.seed_tokens or []),
            bars=len(bars),
            config={
                "population": cfg.population,
                "generations": cfg.generations,
                "max_depth": cfg.max_depth,
                "train_ratio": cfg.train_ratio,
                "walk_forward_folds": cfg.walk_forward_folds,
                "cost": cost,
            },
            champions=champion_payload(final_champions, stamp_kernel=True),
        )
    except Exception:
        logger.debug("挖掘试验登记写入失败（不影响任务完成）", exc_info=True)


def _finalize(
    bars: list,
    timeframe: str,
    cfg: SearchConfig,
    seed_best: list,
) -> list:
    """同步：用 seed_best 直接走 _dedup_top 得到最终冠军（search_stepwise 已累积）"""
    from app.services.factor_lab.search import _dedup_top
    from app.services.factor_lab.scoring.walk_forward import (
        MIN_TEST_BARS,
        split_bars,
    )

    if cfg.test_recent_bars > 0:
        cut = max(MIN_TEST_BARS, len(bars) - cfg.test_recent_bars)
        if cut < MIN_TEST_BARS or (len(bars) - cut) < MIN_TEST_BARS:
            train_bars, test_bars = list(bars), []
        else:
            train_bars, test_bars = list(bars[:cut]), list(bars[cut:])
    elif cfg.train_ratio > 0.0:
        train_bars, test_bars = split_bars(bars, cfg.train_ratio)
    else:
        train_bars, test_bars = list(bars), []
    use_test = bool(test_bars) and len(test_bars) >= MIN_TEST_BARS
    return _dedup_top(
        list(seed_best),
        cfg.top_n,
        test_bars=test_bars,
        timeframe=timeframe,
        cost=cfg.cost,
        use_test=use_test,
        walk_forward_folds=cfg.walk_forward_folds,
        all_bars=bars,
        train_bars=train_bars,
    )


def _champions_to_dicts(champions: list) -> list[dict]:
    """Champion dataclass 列表 → 可 JSONB 序列化的 dict 列表"""
    out = []
    for c in champions:
        out.append(
            {
                "tokens": list(c.tokens),
                "text": c.text,
                "composite": float(c.composite),
                "metrics": dict(c.metrics),
            }
        )
    return out


async def _mark_failed(session: Any, task: FactorMiningTask, msg: str) -> None:
    """标记任务失败"""
    task.status = "failed"
    task.error_msg = msg[:500]
    task.running_worker = None
    task.completed_at = _now()
    task.updated_at = _now()
    try:
        await session.commit()
    except Exception:
        await session.rollback()


def _resolve_cost(symbol: str, bars: list) -> float:
    """按品种推导成本率（与 factor_lab common.resolve_cost 同口径）"""
    valid = [float(b.get("close") or 0) for b in bars]
    valid = [c for c in valid if c > 0]
    price = sum(valid) / len(valid) if valid else 0.0
    return turnover_cost_rate(symbol, price, DEFAULT_SLIPPAGE_TICKS)
