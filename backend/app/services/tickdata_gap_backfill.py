"""tickdata 缺口定向回填 —— 非主力合约在 pg-tickdata 的近段缺数自动补

背景(2026-08-23 j2701/v2701 缺段):vvtr_tickdata_sync 只同步主力合约,
远月合约成为主力前无人写入,缺段长期无感。本任务每日 03:30 扫描
「近 30 天有数据但 60m 最新落后 >3 天」的非主力合约,经 VVTR 定向回填
(幂等 upsert),把缺段问题制度化闭环。

预算防护:每轮最多 6 个合约,03:30 错开 01:00 权威修正与盘中整点主力
同步,不挤全局 1 rps。VVTR 多分钟单次上限(60m=460/15m=920 根)覆盖
近两月内缺口;更早缺口记日志留待人工(如 j2701 的 6-16~6-28 上游本无)。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from app.services.kline_task_status import log as task_log
from app.services.kline_task_status import report as task_report

logger = logging.getLogger("qihuo.tickdata_backfill")

TASK_NAME = "tickdata_gap_backfill"

# 每轮最多回填合约数(1 rps 预算:6 合约 × 6 周期 ≈ 40+ 请求,03:30 空闲期可接受)
_MAX_BACKFILL_PER_ROUND = 6

# 单合约回填天数上限(VVTR 单次根数上限决定的实用边界)
_MAX_BACKFILL_DAYS = 60

# 触发时间(北京时间)
_TRIGGER_HOUR = 3
_TRIGGER_MINUTE = 30


async def find_gap_contracts() -> list[tuple[str, int]]:
    """扫描 pg-tickdata:近 30 天有数据但 60m 最新落后 >3 天的非 9999/8888 合约

    返回 [(instrument, lag_days)] 按 lag 降序(限 30 条)。复用 tickdata_kline
    连接池;未配置/失败返回 [](降级不报错)。「近 30 天有数据」排除已到期
    老合约,「落后 >3 天」容忍周末/节假日。
    """
    from app.data.tickdata_kline import _get_pool

    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT instrument, (current_date - max(ts)::date) lag "
                "FROM kline "
                "WHERE period = 60 AND ts >= current_date - 30 "
                "  AND right(instrument, 4) NOT IN ('9999', '8888') "
                "GROUP BY instrument "
                "HAVING max(ts) < current_date - 3 "
                "ORDER BY lag DESC LIMIT 30",
                timeout=30,
            )
    except Exception:
        logger.warning("缺口扫描查询失败", exc_info=True)
        return []
    return [(r["instrument"], int(r["lag"])) for r in rows]


async def _master_set(redis_conn: aioredis.Redis) -> set[str]:
    """当前主力合约集合(小写);读取失败返回空集(跳过主力过滤)"""
    try:
        from app.services.contract_master import get_master_contracts

        entries = await get_master_contracts(redis_conn)
        return {
            str(e.get("master") or "").strip().lower()
            for e in (entries or {}).values()
            if isinstance(e, dict)
        }
    except Exception:
        logger.debug("主力集合读取失败,跳过主力过滤", exc_info=True)
        return set()


async def backfill_gaps_once(
    redis_conn: aioredis.Redis,
    session_factory: Any = None,
    trigger: str = "scheduled_0330",
) -> dict[str, Any]:
    """执行一轮缺口回填:扫描 → 过滤主力 → 逐合约定向回填 → 上报状态/日志"""
    from app.services.vvtr_tickdata_sync import sync_masters_to_tickdata

    stats: dict[str, Any] = {
        "trigger": trigger,
        "scanned": 0,
        "candidates": 0,
        "backfilled": 0,
        "written_rows": 0,
        "skipped_masters": 0,
    }
    await task_report(redis_conn, TASK_NAME, running=True, trigger=trigger)
    try:
        gaps = await find_gap_contracts()
        stats["scanned"] = len(gaps)

        masters = await _master_set(redis_conn)
        if masters:
            before = len(gaps)
            gaps = [(ins, lag) for ins, lag in gaps if ins not in masters]
            stats["skipped_masters"] = before - len(gaps)

        candidates = gaps[:_MAX_BACKFILL_PER_ROUND]
        stats["candidates"] = len(candidates)

        for instrument, lag in candidates:
            days = min(_MAX_BACKFILL_DAYS, lag + 5)
            per = await sync_masters_to_tickdata(
                redis_conn, days=days, symbols=[instrument]
            )
            written = sum(per.values())
            stats["backfilled"] += 1
            stats["written_rows"] += written
            if written:
                await task_log(
                    redis_conn, TASK_NAME, "info",
                    f"回填 {instrument}(lag {lag} 天,窗口 {days} 天):写入 {written} 行",
                    instrument=instrument, lag_days=lag, written=written,
                )
            else:
                await task_log(
                    redis_conn, TASK_NAME, "warn",
                    f"{instrument} 回填 0 行(VVTR 无该段数据,留待人工)",
                    instrument=instrument,
                )

        await task_report(
            redis_conn, TASK_NAME,
            running=False,
            last_stats=stats,
            last_error=None,
        )
        await task_log(
            redis_conn, TASK_NAME, "info",
            "缺口回填完成:扫描 {scanned} 候选 {candidates} 回填 {backfilled} "
            "合约共 {written_rows} 行(排除主力 {skipped_masters})".format(**stats),
            **stats,
        )
    except Exception as exc:
        stats["error"] = str(exc)
        await task_report(
            redis_conn, TASK_NAME,
            running=False, last_stats=stats, last_error=str(exc),
        )
        await task_log(redis_conn, TASK_NAME, "error", f"缺口回填异常: {exc}")
        logger.exception("tickdata 缺口回填异常")
    return stats


def _sync_owner_disabled() -> bool:
    """本机是否为非 owner(与 vvtr_tickdata_sync 同一开关)。

    VVTR 1 rps 预算全局共享:tickdata 同步(主力每小时 + 缺口回填)只能
    由 owner 端(生产 58)发起;其他端设 VVTR_TICKDATA_SYNC_DISABLED=1。
    """
    import os

    return os.getenv("VVTR_TICKDATA_SYNC_DISABLED", "").strip() in ("1", "true", "True")


async def tickdata_gap_backfill_loop(
    redis_conn: aioredis.Redis,
    session_factory: Any = None,
) -> None:
    """每日 03:30(北京)触发一轮缺口回填;60s 轮询 + 当日去重(模式同 kline_sync)"""
    from app.services.kline_task_status import is_paused, report as task_report
    from app.services.trading_hours import beijing_now

    if _sync_owner_disabled():
        logger.info(
            "VVTR_TICKDATA_SYNC_DISABLED=1：本机跳过缺口回填(由 owner 端独跑)"
        )
        await task_report(redis_conn, TASK_NAME, disabled=True)
        return
    last_fired_day = ""
    await asyncio.sleep(45)  # 错峰启动
    while True:
        try:
            if await is_paused(redis_conn, TASK_NAME):
                await task_report(redis_conn, TASK_NAME, paused=True)
            else:
                bj = beijing_now()
                today = bj.strftime("%Y-%m-%d")
                if (
                    bj.hour == _TRIGGER_HOUR
                    and bj.minute == _TRIGGER_MINUTE
                    and last_fired_day != today
                ):
                    last_fired_day = today
                    now_iso = (
                        datetime.now(timezone(timedelta(hours=8)))
                        .replace(tzinfo=None)
                        .isoformat(timespec="seconds")
                    )
                    await task_report(
                        redis_conn, TASK_NAME,
                        running=True, last_start=now_iso, paused=False,
                    )
                    await backfill_gaps_once(redis_conn, session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("tickdata_gap_backfill_loop 异常")
        await asyncio.sleep(60)
