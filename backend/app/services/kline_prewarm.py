"""K 线盘前预热：主力合约 × 全周期填 Redis 历史缓存

openspec 2026-08-25-kline-prewarm-source-hardening。背景：分时周期切换
已秒开（PR #204），剩余卡点是冷主力合约首开（走完整源链 2-18s）。预热
让每个交易日开盘前全部主力各周期缓存就绪，切换必然命中。

节奏约束：项间 ≥1.2s——sina 桥守卫（connectors/sina/guard.py）之外再
限一档，两台机器各自预热本地 Redis、上游压力由桥缓存/单飞去重。
33 默认 08:40 起；58 用 KLINE_PREWARM_START=08:55 错峰 15min。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.kline_prewarm")

_BJ_TZ = timezone(timedelta(hours=8))

# 预热矩阵：全部主力 × 分钟周期 + 日线（与 bundle 前端缓存周期对齐再加 1d）
PREWARM_PERIODS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "60m", "1d")
# 项间节奏（69×6=414 项 ≈ 8.3min）：压在桥守卫 4rps 之下，双机错峰后
# 上游合计 < 1.5rps，远离 12 并发压测触发的悬崖线
ITEM_INTERVAL = 1.2
# 容错：单项失败不中断；连续 10 项失败暂停 60s（大概率上游熔断/限流）
FAIL_PAUSE_COUNT = 10
FAIL_PAUSE_SECONDS = 60.0
LOOP_TICK = 30.0
DONE_KEY_PREFIX = "kline:prewarm:done:"
DONE_KEY_TTL = 2 * 86400
LIMIT_PER_PERIOD = 240
# 状态快照存 Redis（backend 多 worker：预热在选主 worker，status 端点
# 可能打到任意 worker，模块级状态不成立——对齐 kline_task_status 惯例）
STATUS_KEY = "kline:prewarm:status"
STATUS_TTL = 7 * 86400

# 模块级状态（单进程回退/测试用；对外读取走 prewarm_status(redis)）
_status: dict[str, Any] = {
    "running": False,
    "last_date": "",
    "done": 0,
    "total": 0,
    "last_error": "",
    "started_at": None,
    "finished_at": None,
}


async def _publish(redis_conn: aioredis.Redis | None, **fields: Any) -> None:
    """状态写入模块字典 + Redis（跨 worker 可读）；Redis 故败不影响预热"""
    _status.update(fields)
    if redis_conn is None:
        return
    try:
        mapping = {k: json.dumps(v, ensure_ascii=False) for k, v in fields.items()}
        await redis_conn.hset(STATUS_KEY, mapping=mapping)
        await redis_conn.expire(STATUS_KEY, STATUS_TTL)
    except Exception:
        logger.debug("[prewarm] 状态发布 Redis 失败（不影响预热）", exc_info=True)


async def prewarm_status(redis_conn: aioredis.Redis | None = None) -> dict[str, Any]:
    """读状态快照：优先 Redis（跨 worker），无/失败回退模块字典"""
    base = dict(_status)
    if redis_conn is not None:
        try:
            raw = await redis_conn.hgetall(STATUS_KEY)
            for k, v in (raw or {}).items():
                key = k if isinstance(k, str) else k.decode()
                val = v if isinstance(v, str) else v.decode()
                try:
                    base[key] = json.loads(val)
                except (ValueError, TypeError):
                    base[key] = val
        except Exception:
            logger.debug("[prewarm] 状态读取 Redis 失败，回退本进程快照")
    return base


def prewarm_enabled() -> bool:
    return os.getenv("KLINE_PREWARM_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _start_hhmm() -> tuple[int, int]:
    """预热起点（北京时间 HH:MM；58 部署设 KLINE_PREWARM_START=08:55 错峰）"""
    raw = os.getenv("KLINE_PREWARM_START", "08:40").strip()
    try:
        hh, mm = raw.split(":", 1)
        return int(hh), int(mm)
    except (ValueError, IndexError):
        logger.warning("KLINE_PREWARM_START 解析失败（%s），回退 08:40", raw)
        return 8, 40


async def _master_symbols(redis_conn: aioredis.Redis) -> list[str]:
    from app.services.contract_master import get_master_contracts

    masters = await get_master_contracts(redis_conn)
    symbols = {
        str(entry.get("master") or "").lower()
        for entry in (masters or {}).values()
        if entry.get("master")
    }
    return sorted(symbols)


async def run_prewarm_once(redis_conn: aioredis.Redis) -> int:
    """执行一轮预热；返回完成项数（含失败项）。幂等性由调用方（判重键）保证"""
    from app.services.kline import get_kline_history

    symbols = await _master_symbols(redis_conn)
    if not symbols:
        # 主力表为空（Redis 被清/未就绪）：跳过并告警，不用过期清单预热
        await _publish(redis_conn, running=False, last_error="主力清单为空，跳过本轮预热")
        logger.warning("[prewarm] 主力清单为空，跳过本轮（等下一轮循环）")
        return 0

    total = len(symbols) * len(PREWARM_PERIODS)
    await _publish(
        redis_conn,
        running=True,
        done=0,
        total=total,
        last_error="",
        started_at=datetime.now(_BJ_TZ).isoformat(timespec="seconds"),
        finished_at=None,
    )
    logger.info("[prewarm] 开始：%d 主力 × %d 周期 = %d 项", len(symbols), len(PREWARM_PERIODS), total)

    done = 0
    cont_fail = 0
    fails = 0
    for symbol in symbols:
        for period in PREWARM_PERIODS:
            try:
                await get_kline_history(redis_conn, symbol, period, limit=LIMIT_PER_PERIOD)
                cont_fail = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                fails += 1
                cont_fail += 1
                logger.warning("[prewarm] %s %s 失败（第 %d 项失败）", symbol, period, fails)
                if cont_fail >= FAIL_PAUSE_COUNT:
                    logger.warning(
                        "[prewarm] 连续 %d 项失败，暂停 %.0fs", cont_fail, FAIL_PAUSE_SECONDS
                    )
                    await asyncio.sleep(FAIL_PAUSE_SECONDS)
                    cont_fail = 0
            done += 1
            await _publish(
                redis_conn,
                done=done,
                last_error=f"{symbol} {period} 取数失败" if cont_fail else "",
            )
            await asyncio.sleep(ITEM_INTERVAL)

    await _publish(
        redis_conn,
        running=False,
        finished_at=datetime.now(_BJ_TZ).isoformat(timespec="seconds"),
    )
    logger.info(
        "[prewarm] 完成：%d/%d 项（失败 %d）", done, total, fails
    )
    return done


async def _maybe_prewarm(
    redis_conn: aioredis.Redis, bj: datetime | None = None
) -> None:
    """到点且当日未跑过 → 执行一轮并写判重键（含启动补跑：过点未跑即补）

    bj 可注入（测试用），默认北京时间当前。
    判重用 SET NX 原子抢当日执行权：多 worker / 重复注册的循环 /
    并发进入本函数的所有竞态都只有一个赢家（2026-08-25 实测曾出现
    两个执行体交错写进度导致 done 回退）。
    """
    bj = bj or datetime.now(_BJ_TZ)
    today = bj.strftime("%Y-%m-%d")
    hh, mm = _start_hhmm()

    # 周末不预热（节假日无日历，源返回上一交易日数据，预热无害但无意义）
    if bj.weekday() >= 5:
        return
    if (bj.hour, bj.minute) < (hh, mm):
        return
    done_key = f"{DONE_KEY_PREFIX}{today}"

    claimed: bool | None
    try:
        claimed = bool(
            await redis_conn.set(done_key, "running", nx=True, ex=DONE_KEY_TTL)
        )
    except Exception:
        claimed = None  # Redis 异常：退回进程内守卫（尽力而为）
    if claimed is False:
        return  # 今天已有人跑过/在跑
    if claimed is None and _status.get("last_date") == today:
        return
    _status["last_date"] = today
    try:
        await run_prewarm_once(redis_conn)
    except asyncio.CancelledError:
        # 中断（部署重启）：清占位并释放执行权键，重启后补跑
        _status["last_date"] = ""
        try:
            await redis_conn.delete(done_key)
        except Exception:
            pass
        raise
    # 完成态落定（部分失败也算完成，错误在 status 可观测，避免整日重试）
    try:
        await redis_conn.set(done_key, "1", ex=DONE_KEY_TTL)
    except Exception:
        logger.warning("[prewarm] 判重键写入失败（重启后会重跑一轮，幂等无害）")


async def kline_prewarm_loop(redis_conn: aioredis.Redis) -> None:
    """预热主循环（scheduler 注册；选主 worker 运行）"""
    if not prewarm_enabled():
        logger.info("[prewarm] KLINE_PREWARM_ENABLED 关闭，预热任务退出")
        return
    # 启动等 60s：主力识别首轮缓存 / sina 桥 / relay 先就绪
    await asyncio.sleep(60)
    logger.info(
        "[prewarm] 循环启动（每交易日 %02d:%02d 起，主力 × %d 周期）",
        *_start_hhmm(),
        len(PREWARM_PERIODS),
    )
    while True:
        try:
            await _maybe_prewarm(redis_conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[prewarm] 循环异常")
        await asyncio.sleep(LOOP_TICK)
