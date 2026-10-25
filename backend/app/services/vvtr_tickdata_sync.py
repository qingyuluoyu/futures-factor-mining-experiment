"""VVTR K线 → pg-tickdata 每日自动同步 + 回填

- sync_masters_to_tickdata(days)：主力 × 全周期从 VVTR 拉近 N 天并 upsert
  （幂等，ON CONFLICT 更新；请求走全局 1 rps 节流）
- vvtr_tickdata_sync_loop：scheduler 常驻循环——交易时段每小时一轮
  同步近 2 天（覆盖"每天自动写入"+断档自愈），休市每 6 小时一轮对账；
  另有每日定点 01:00 + 15:05 全量同步（两段式第一段：定点拉完 vvtr，
  02:00 + 15:30 主程序才读 pg-tickdata 跑权威修正，见 kline_sync）
- 回填脚本：backend/scripts/backfill-tickdata-vvtr.py（近 7 天）
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.vvtr.tickdata_sync")

_BJ_TZ = timezone(timedelta(hours=8))

# 同步周期（分钟线全量 + 日线）
SYNC_PERIODS = ("1m", "5m", "15m", "30m", "60m", "1d")

# 常驻循环：盘中每小时一轮，休市每 6 小时一轮
_TRADING_INTERVAL = 3600.0
_IDLE_INTERVAL = 6 * 3600.0

# 每日定点全量同步（北京时间）：修正(02:00/15:30)前保证 pg-tickdata 新鲜
# ——盘中每小时节奏在收盘/夜盘收尾时可能错过最后一段（上轮整点跑在
# 收盘前），定点兜底补齐，修正读到的才是完整权威数据
_DAILY_POINTS: tuple[tuple[int, int], ...] = ((1, 0), (15, 5))

# 跨请求间隔（配合全局 1.1s 节流，稍作停顿降低长时间占用）
_REQUEST_GAP = 1.3


def _seconds_to_next_point(bj: datetime, fired: dict[str, str]) -> float:
    """距下一个「今天尚未跑过」定点的时间（秒）；都已跑过则看明天"""
    now_naive = bj.replace(tzinfo=None)
    candidates: list[float] = []
    today = bj.strftime("%Y-%m-%d")
    for hh, mm in _DAILY_POINTS:
        key = f"{hh:02d}{mm:02d}"
        for day_offset in (0, 1):
            pt = bj.replace(
                hour=hh, minute=mm, second=0, microsecond=0, tzinfo=None
            ) + timedelta(days=day_offset)
            delta = (pt - now_naive).total_seconds()
            if delta > 0 and (day_offset == 1 or fired.get(key) != today):
                candidates.append(delta)
    return min(candidates) if candidates else 3600.0


def _due_daily_point(bj: datetime, fired: dict[str, str]) -> str | None:
    """返回到期的定点标签（并标记当天已跑）；无到期返回 None

    启动时调用方应先把「今天已过点」预标记为已跑，防止重启补跑。
    """
    today = bj.strftime("%Y-%m-%d")
    for hh, mm in _DAILY_POINTS:
        key = f"{hh:02d}{mm:02d}"
        pt = bj.replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=None)
        if bj.replace(tzinfo=None) >= pt and fired.get(key) != today:
            fired[key] = today
            return f"{hh:02d}:{mm:02d}"
    return None


async def _sync_symbols(redis_conn: aioredis.Redis) -> list[str]:
    """同步对象：主力识别缓存（小写真实主力）

    缓存为空（部署重建 Redis 后首轮常见，2026-08-24 58 两次发布均触发：
    主力缓存被清空 → 同步"0 合约 × 0 请求"空转 → pg-tickdata 当日断档）
    时回退静态合约表，绝不空转。"""
    try:
        from app.services.contract_master import get_master_contracts

        masters = await get_master_contracts(redis_conn)
        symbols = [
            str(entry.get("master") or "")
            for entry in (masters or {}).values()
            if isinstance(entry, dict)
        ]
        symbols = [s.strip().lower() for s in symbols if s.strip()]
        if symbols:
            return symbols
        logger.warning(
            "主力识别缓存为空（部署重建 Redis 后首轮？），tickdata 同步回退静态合约表"
        )
    except Exception:
        logger.debug("主力缓存读取失败，回退静态合约表", exc_info=True)
    from app.data.contracts import ALL_CONTRACTS

    return [c["symbol"].lower() for c in ALL_CONTRACTS]


def _sync_days(period: str, days: int) -> int:
    """按周期与天数推算拉取 limit 根数（每交易日 bar 数 × 天数 / 0.7 占比）"""
    per_day = {"1m": 345, "5m": 69, "15m": 23, "30m": 12, "60m": 6}.get(period, 1)
    return int(per_day * days / 0.7) + 32


async def sync_masters_to_tickdata(
    redis_conn: aioredis.Redis,
    days: float = 2.0,
    symbols: list[str] | None = None,
    session_factory: Any = None,
) -> dict[str, int]:
    """主力 × 全周期：VVTR 拉近 days 天 → upsert pg-tickdata

    返回 {period: written_rows}；请求串行且走全局节流。
    """
    from app.connectors.vvtr.config import (
        _load_config_from_pg,
        get_vvtr_runtime_config,
    )
    from app.connectors.vvtr.http_client import fetch_vvtr_kline
    from app.data.tickdata_writer import persist_bars

    if session_factory is not None:
        cfg = await get_vvtr_runtime_config(session_factory)
    else:
        # 无 session factory（脚本/独立运行）：asyncpg 直读配置
        try:
            cfg = await _load_config_from_pg()
        except Exception:
            cfg = None
    if cfg is None or not cfg.api_key or not cfg.kline_enabled:
        logger.warning("VVTR K线未启用或配置读取失败，跳过 tickdata 同步")
        return {}

    syms = symbols if symbols is not None else await _sync_symbols(redis_conn)
    stats: dict[str, int] = {p: 0 for p in SYNC_PERIODS}
    total_requests = 0
    for symbol in syms:
        for period in SYNC_PERIODS:
            try:
                bars = await asyncio.to_thread(
                    fetch_vvtr_kline,
                    cfg.api_key,
                    symbol,
                    period,
                    _sync_days(period, days),
                    cfg.base_url,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("tickdata 同步拉取失败 %s %s", symbol, period)
                bars = []
            total_requests += 1
            if bars:
                # 只保留近 days 天（fetcher 窗口可能更宽）
                if period == "1d":
                    cutoff = (
                        datetime.now(_BJ_TZ) - timedelta(days=days * 1.6 + 3)
                    ).strftime("%Y-%m-%d")
                    bars = [b for b in bars if b["time"] >= cutoff]
                else:
                    cutoff = (
                        datetime.now(_BJ_TZ) - timedelta(days=days + 1)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    bars = [b for b in bars if b["time"] >= cutoff]
                written = await persist_bars(symbol, period, bars, force=True)
                stats[period] += written
            await asyncio.sleep(_REQUEST_GAP)
    logger.info(
        "tickdata 同步完成：%d 合约 × %d 周期（%d 次请求），写入 %s",
        len(syms), len(SYNC_PERIODS), total_requests,
        {k: v for k, v in stats.items() if v},
    )
    return stats


def _regular_due(
    now_mono: float, next_regular: float, last_run_mono: float, trading: bool
) -> bool:
    """常规轮是否到期：到点，或（进入交易时段且距上轮 ≥ 盘中间隔）提前。

    休市期排下的 6h 对账轮在夜盘开盘后应提前恢复盘中节奏
    （2026-08-24 58 部署重启于休市：下轮排 +6h 到次日凌晨，
    21:00 夜盘开盘整晚不触发，pg-tickdata 当日日线断档）。
    """
    if now_mono >= next_regular:
        return True
    return trading and now_mono >= last_run_mono + _TRADING_INTERVAL


async def vvtr_tickdata_sync_loop(
    redis_conn: aioredis.Redis,
    session_factory: Any = None,
) -> None:
    """常驻循环：盘中每小时同步近 2 天，休市每 6 小时对账一轮
    + 每日定点 01:00 / 15:05 全量同步（两段式第一段）

    多台后端共享一个 VVTR key（经 relay）时，后台全量对账同步只能有
    一个 owner（每轮 69 合约 × 5 周期 ≈ 700 次 1 rps 请求，双跑会把
    预算打满拖慢用户 K线）。非 owner 端设 env VVTR_TICKDATA_SYNC_DISABLED=1
    跳过本循环；用户请求的写通落库（persist_bars_task）不受影响，
    历史回溯统一读共享 pg-tickdata。

    定点语义：01:00 / 15:05 到点立即全量同步（每天各一次，去重）；
    启动/重启时今天已过点的不补跑（避免部署触发额外全量拉取 vvtr）。
    定点 + 常规节奏任一到期即执行一轮。

    管理端可经 kline:task:vvtr_tickdata_sync:paused 暂停；每轮结果上报
    kline:task:vvtr_tickdata_sync:status(供 /admin/kline-tasks 展示)。
    """
    import os

    from app.services.kline_task_status import (
        is_paused,
        log as task_log,
        report as task_report,
    )
    from app.services.trading_hours import beijing_now, is_trading_hours

    if os.getenv("VVTR_TICKDATA_SYNC_DISABLED", "").strip() in ("1", "true", "True"):
        logger.info(
            "VVTR_TICKDATA_SYNC_DISABLED=1：本机跳过 tickdata 定时同步"
            "（同步由共享库所在端独跑）"
        )
        await task_report(redis_conn, "vvtr_tickdata_sync", disabled=True)
        return

    point_fired: dict[str, str] = {}
    # 启动预标记：今天已过点视为已跑（重启不补跑，防部署触发全量拉 vvtr）
    bj = beijing_now()
    today = bj.strftime("%Y-%m-%d")
    for hh, mm in _DAILY_POINTS:
        pt = bj.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if bj >= pt:
            point_fired[f"{hh:02d}{mm:02d}"] = today

    next_regular = 0.0  # monotonic；0 = 启动立即跑一轮（保持原行为）
    last_run_mono = time.monotonic()
    while True:
        try:
            bj = beijing_now()
            due_point = _due_daily_point(bj, point_fired)
            regular_due = _regular_due(
                time.monotonic(), next_regular, last_run_mono, is_trading_hours()
            )
            if due_point or regular_due:
                if due_point:
                    logger.info("tickdata 定点同步触发 %s（两段式第一段）", due_point)
                trading = is_trading_hours()
                try:
                    if await is_paused(redis_conn, "vvtr_tickdata_sync"):
                        await task_report(
                            redis_conn, "vvtr_tickdata_sync", paused=True
                        )
                    else:
                        stats = await sync_masters_to_tickdata(
                            redis_conn, days=2.0, session_factory=session_factory
                        )
                        written = sum(stats.values())
                        await task_report(
                            redis_conn, "vvtr_tickdata_sync",
                            paused=False, last_written=written,
                            last_stats=stats, last_error=None,
                        )
                        if written:
                            await task_log(
                                redis_conn, "vvtr_tickdata_sync", "info",
                                f"主力同步写入 {written} 行"
                                + (f"（定点 {due_point}）" if due_point else ""),
                                **stats,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("tickdata 定时同步异常")
                    await task_report(
                        redis_conn, "vvtr_tickdata_sync", last_error=str(exc)
                    )
                # 本轮结束后重置常规节奏时钟
                last_run_mono = time.monotonic()
                next_regular = last_run_mono + (
                    _TRADING_INTERVAL if is_trading_hours() else _IDLE_INTERVAL
                )
            # 睡到：常规下一轮 / 下一个未跑定点，取更近者；
            # 分片 ≤5min，醒来重估交易状态以支持上面的盘中提前触发
            deadline = min(
                next_regular,
                time.monotonic() + _seconds_to_next_point(beijing_now(), point_fired),
            )
            while True:
                remain = deadline - time.monotonic()
                if remain <= 0:
                    break
                await asyncio.sleep(min(300.0, remain))
                if (
                    is_trading_hours()
                    and time.monotonic() >= last_run_mono + _TRADING_INTERVAL
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("tickdata 同步循环异常")
            await asyncio.sleep(60.0)
