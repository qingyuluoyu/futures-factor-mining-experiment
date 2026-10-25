"""K 线历史数据权威修正 —— 第二段：读 pg-tickdata 修正主 PG

两段式错峰设计（vvtr 分发源只被打一次，保护其 1 req/s 限频预算）：
- 第一段 01:00 + 15:05：vvtr_tickdata_sync 从 vvtr 分发源拉取主力全周期
  写入共享库 pg-tickdata（58:5434）；
- 第二段 02:00 + 15:30（本模块）：主程序读 pg-tickdata 的权威数据修正
  主 PG——**不直连 vvtr**。

- 分钟线（1m/5m/15m/30m/60m）→ kline_minute 表（ON CONFLICT UPDATE）
- 日线（1d）→ kline_history 表（ON CONFLICT UPDATE）
- 修正后并入 Redis 历史缓存（kline:history:*），客户端直接读到修正结果

修正对象：主力缓存 ∪ kline_minute 近 3 天合约（实时固化写入面）；
不在 pg-tickdata 中的合约（tickdata 同步只覆盖主力 + 用户请求写通）读
不到数据自动跳过。
状态：修正过程实时写 Redis（kline:sync:status + kline:sync:log），供管理员查看。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.kline_sync")

# 修正的周期：分钟线写 kline_minute，日线写 kline_history
SYNC_PERIODS_MINUTE = ["1m", "5m", "15m", "30m", "60m"]
SYNC_PERIOD_DAILY = "1d"

# 单次读取根数上限（pg-tickdata；1m≈3天、15m≈40天、日线全历史）
_SYNC_FETCH_LIMIT = 1024

# 品种间间隔（秒）：读本地/内网 PG 无需 vvtr 1rps 节流，仅给库留喘息
_INTER_CODE_SLEEP = 0.2

# Redis 键
_SYNC_STATUS_KEY = "kline:sync:status"
_SYNC_LOG_KEY = "kline:sync:log"
_SYNC_TTL = 7 * 24 * 3600  # 7 天
_SYNC_LOG_MAX = 200  # 保留最近 200 条品种级日志
# 僵尸 running 状态判定阈值：started_at 超过此秒数视为上一代进程残留
_STALE_THRESHOLD_SEC = 3600  # 1 小时

_BJ_TZ = timezone(timedelta(hours=8))


def _unique_codes() -> list[dict[str, str]]:
    """从 ALL_CONTRACTS 去重提取 {code, exchange}（保序）"""
    from app.data.contracts import ALL_CONTRACTS

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for c in ALL_CONTRACTS:
        code = str(c.get("code") or "").strip()
        exchange = str(c.get("exchange") or "").strip()
        if code and exchange and code not in seen:
            seen.add(code)
            out.append({"code": code.lower(), "exchange": exchange})
    return out


def _resolve_main_symbol(code: str) -> str:
    """从 ALL_CONTRACTS 找品种 code 对应的第一个合约符号（主力）"""
    try:
        from app.data.contracts import ALL_CONTRACTS

        for c in ALL_CONTRACTS:
            if str(c.get("code") or "").lower() == code.lower():
                return str(c.get("symbol") or code)
    except Exception:
        pass
    return code


def _now_bj_iso() -> str:
    """当前北京时间 ISO 格式"""
    return datetime.now(_BJ_TZ).isoformat()


# ========== Redis 状态写入 ==========


async def _write_sync_status(
    redis_conn: aioredis.Redis | None,
    status: dict[str, Any],
) -> None:
    """写修正汇总状态到 Redis"""
    if redis_conn is None:
        return
    try:
        await redis_conn.set(_SYNC_STATUS_KEY, json.dumps(status, ensure_ascii=False), ex=_SYNC_TTL)
    except Exception:
        logger.debug("写 kline:sync:status 失败", exc_info=True)


async def _cleanup_stale_running_status(
    redis_conn: aioredis.Redis | None,
) -> None:
    """启动时清理上一代进程残留的 running 状态

    部署重启容器会 SIGKILL 正在跑的 sync 任务，进程没机会写 finished，
    kline:sync:status 卡在 running（TTL 7 天），后台面板一直显示"修正中"。
    若 status=running 且 started_at 超过 _STALE_THRESHOLD_SEC，视为僵尸，
    自动标记 error 并补全 stats/errors 字段（防前端访问缺失字段白屏）。
    """
    if redis_conn is None:
        return
    try:
        raw = await redis_conn.get(_SYNC_STATUS_KEY)
        if not raw:
            return
        data = json.loads(raw)
        if data.get("status") != "running":
            return
        started_at = data.get("started_at")
        if not started_at:
            return
        try:
            dt = datetime.fromisoformat(str(started_at))
        except (ValueError, TypeError):
            return
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_BJ_TZ)
        age_sec = (datetime.now(_BJ_TZ) - dt).total_seconds()
        if age_sec < _STALE_THRESHOLD_SEC:
            return  # <1h，可能是当前进程刚起的 sync，不清理
        data["status"] = "error"
        data["finished_at"] = _now_bj_iso()
        data.setdefault("stats", {})
        data.setdefault("errors", [])
        errs = data["errors"]
        if isinstance(errs, list) and not any(
            "interrupted" in str(e) or "stale" in str(e) for e in errs
        ):
            errs.append("stale: interrupted by redeploy, auto-cleared")
        await redis_conn.set(
            _SYNC_STATUS_KEY,
            json.dumps(data, ensure_ascii=False),
            ex=_SYNC_TTL,
        )
        logger.info(
            "清理僵尸 sync 状态 started_at=%s age=%.0fs（标记 error）",
            started_at,
            age_sec,
        )
    except Exception:
        logger.debug("清理僵尸 sync 状态失败", exc_info=True)


async def _push_sync_log(
    redis_conn: aioredis.Redis | None,
    entry: dict[str, Any],
) -> None:
    """写品种级日志到 Redis List（LPUSH + LTRIM）"""
    if redis_conn is None:
        return
    try:
        await redis_conn.lpush(_SYNC_LOG_KEY, json.dumps(entry, ensure_ascii=False))
        await redis_conn.ltrim(_SYNC_LOG_KEY, 0, _SYNC_LOG_MAX - 1)
        await redis_conn.expire(_SYNC_LOG_KEY, _SYNC_TTL)
    except Exception:
        logger.debug("写 kline:sync:log 失败", exc_info=True)


# ========== 核心修正逻辑 ==========

# 权威修正写入历史缓存后的最短保留期：交易时段普通缓存 TTL 仅 5-10 分钟，
# 过期重建会走 eastmoney/sina 主链——非权威值会把刚修正好的权威 bar 覆盖
# 回去（2026-08-22 实测：58 东财不通降级新浪，新浪对 au2612 返回脏价，
# 缓存 6h 过期后图面出现 24 元断崖）。修正后延长 TTL 到 24 小时，覆盖
# 隔夜与周末访问窗口，重建只发生在下一交易日权威同步之后。
_AUTH_CACHE_TTL = 24 * 3600


async def _merge_corrected_into_cache(
    redis_conn: aioredis.Redis | None,
    symbol: str,
    period: str,
    corrected: list[dict[str, Any]],
) -> bool:
    """把权威修正 bar 并入 Redis 历史缓存（kline:history:*）

    time 重叠处以权威 bar 覆盖,保留缓存里更长的历史尾;缓存不存在时
    不预写(修正窗口仅 1024 根,预写会让客户端拿到过短的序列)——留给
    下一次客户端请求按全量源重建,再由下轮修正并入。
    写入 TTL 取 max(常规, _AUTH_CACHE_TTL)：防止交易时段缓存 5-10 分钟
    过期后由非权威源重建、覆盖掉刚并入的权威值。
    返回是否写回。
    """
    if redis_conn is None or not corrected:
        return False
    try:
        from app.services.kline import _get_ttl, _history_cache_key

        key = _history_cache_key(symbol, period)
        raw = await redis_conn.get(key)
        if raw is None:
            return False
        existing = json.loads(raw)
        by_time: dict[str, dict[str, Any]] = {
            str(b.get("time")): b for b in existing if b.get("time")
        }
        for bar in corrected:
            t = str(bar.get("time") or "")
            if t:
                by_time[t] = bar
        merged = [by_time[k] for k in sorted(by_time)]
        await redis_conn.set(
            key,
            json.dumps(merged, ensure_ascii=False),
            ex=max(_get_ttl(period), _AUTH_CACHE_TTL),
        )
        return True
    except Exception:
        logger.debug(
            "修正并入历史缓存失败 %s %s", symbol, period, exc_info=True
        )
        return False


_BACKFILL_1M_DAYS = 3.0


async def backfill_tickdata_1m_from_mainpg(
    session_factory: Any,
    days: float = _BACKFILL_1M_DAYS,
) -> dict[str, int]:
    """主 PG kline_minute（实时固化）近 N 天 1m 差集补全进 pg-tickdata。

    每小时同步 / 写通落库都依赖 vvtr 与 HTTP 源，任一环节尾段缺失时
    永久库会留洞（2026-08-24 pp2701 1m 缺 08-21 21:16-23:00：主 PG
    固化完整，pg-tickdata 断在 21:15）。按 instrument 取 pg-tickdata
    1m 最大 eob，把主 PG 更新的行（含尾部 1 天重写窗口，幂等覆盖）
    经交易日历过滤后 upsert 进永久库。返回 {symbol: rows}。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import text as _text

    from app.data.tickdata_kline import _get_pool
    from app.data.tickdata_writer import persist_bars
    from app.services.kline import _filter_non_trading_bars

    pool = await _get_pool()
    if pool is None:
        return {}

    # 主 PG：近 N 天 1m（ts 为 UTC timestamptz，转北京墙钟 bob）
    # 存储口径注意：kline_minute.ts 的写入方（upsert_minute_bars*）把北京
    # 墙钟 naive 串按 UTC 存入 timestamptz，读取方用 to_char(ts,...) 原样
    # 取回墙钟串（strftime 同口径）才是自洽往返。此前这里误用
    # ts AT TIME ZONE 'Asia/Shanghai'（+8 读出 05:15/07:00 等幻影时刻），
    # 再被交易日过滤整段丢弃，补洞静默失效（2026-08-24 pp2701 1m 缺口
    # 修复后仍复发的原因之一）。
    async with session_factory() as session:
        result = await session.execute(
            _text(
                "SELECT symbol, to_char(ts, 'YYYY-MM-DD HH24:MI:SS') AS ts_bj, "
                "open, high, low, close, volume, hold "
                "FROM kline_minute WHERE period = '1m' "
                f"AND ts >= now() - interval '{int(days * 24)} hours' "
                "ORDER BY symbol, ts"
            )
        )
        rows = result.fetchall()

    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_symbol.setdefault(str(r[0]), []).append(
            {
                "time": str(r[1]),
                "open": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "close": float(r[5]),
                "volume": int(r[6]),
                "open_interest": int(r[7]) if r[7] else None,
            }
        )
    if not by_symbol:
        return {}

    # pg-tickdata：各 instrument 1m 最大 eob（北京墙钟）。
    # 限定近 days+1 天窗口：kline 表按月分区，无界全表聚合会超时
    async with pool.acquire() as conn:
        td_rows = await conn.fetch(
            "SELECT instrument, to_char(max(ts), 'YYYY-MM-DD HH24:MI:SS') "
            f"FROM kline WHERE period = 1 AND ts >= now() - interval '{int(days * 24) + 24} hours' "
            "GROUP BY instrument"
        )
    max_eob = {r[0]: str(r[1] or "") for r in td_rows}

    written: dict[str, int] = {}
    for symbol, bars in by_symbol.items():
        try:
            bound = max_eob.get(symbol)
            if bound:
                # eob 尾部留 1 天重写窗口（幂等覆盖，修正可能的坏尾）
                try:
                    cutoff = (
                        datetime.strptime(bound, "%Y-%m-%d %H:%M:%S")
                        - timedelta(days=1)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    cutoff = ""
                if cutoff:
                    bars = [b for b in bars if b["time"] > cutoff]
            # 周末幽灵 bar 不入永久库
            bars = _filter_non_trading_bars(bars, "1m")
            if not bars:
                continue
            n = await persist_bars(symbol, "1m", bars, force=True)
            if n:
                written[symbol] = n
        except Exception:
            logger.debug("1m 永久库补全失败 %s", symbol, exc_info=True)
    if written:
        logger.info(
            "pg-tickdata 1m 补全（主 PG 固化差集）：%d 合约 %d 行",
            len(written),
            sum(written.values()),
        )
    return written


async def _sync_one_period(
    session_factory: Any,
    code: str,
    symbol: str,
    period: str,
    redis_conn: aioredis.Redis | None = None,
) -> tuple[int, dict[str, int]]:
    """从 pg-tickdata 读取单合约单周期（权威值）写入主 PG,并并入历史缓存

    第二段修正：数据源是共享库 pg-tickdata（由 vvtr_tickdata_sync 于
    01:00/15:05 定点 + 盘中每小时同步），本路径不直连 vvtr 分发源——
    避免修正与 tickdata 同步两路全量打 vvtr 把 1 req/s 预算打满。

    返回 (写入条数, 统计)
    """
    from app.data.tickdata_kline import fetch_tickdata_kline

    # end_time 留空取最近 limit 根；最近视图不做 9999 主力连续拼接
    # （fetch_tickdata_kline 对 end_time 为空的请求只回本合约自身 bar）
    bars = await fetch_tickdata_kline(symbol, period, limit=_SYNC_FETCH_LIMIT)
    if not bars:
        return 0, {"verified": 0, "single": 0, "conflict": 0, "skipped": 0}

    # pg-tickdata（上游 vvtr）即权威源：直接写入，不做多源交叉验证
    if period == SYNC_PERIOD_DAILY:
        from app.services.kline_history import upsert_history_bars

        async with session_factory() as session:
            n = await upsert_history_bars(session, code, bars)
    else:
        from app.services.kline_minute import upsert_minute_bars_update

        async with session_factory() as session:
            n = await upsert_minute_bars_update(
                session, code, symbol, period, bars
            )
    # 修正结果并入历史缓存,客户端(含桌面端)直接读到权威值
    await _merge_corrected_into_cache(redis_conn, symbol, period, bars)
    return n, {
        "verified": len(bars),
        "single": 0,
        "conflict": 0,
        "skipped": 0,
    }


async def _build_sync_items(
    redis_conn: aioredis.Redis | None,
    session_factory: Any,
) -> list[dict[str, str]]:
    """构建修正清单：主力缓存 ∪ 近 3 天库内有实时数据的合约

    必须包含 kline_minute 近期出现过的 symbol——实时固化（persist_closed_bar）
    只写这些合约（含 ma2701 这类非主力），它们正是最需要权威修正的对象。
    主力缓存为空且库也无近期数据时，兜底静态合约表。
    """
    from app.data.contracts import get_code_for_symbol

    items: dict[str, dict[str, str]] = {}

    def _add(symbol: str) -> None:
        symbol = str(symbol or "").strip().lower()
        if not symbol:
            return
        code = get_code_for_symbol(symbol)
        if not code:
            return
        code = code.lower()
        if code not in items:
            items[code] = {
                "code": code,
                "symbol": symbol,
                "exchange": "",
            }

    # 1) 主力识别缓存（各品种当前主力）
    if redis_conn is not None:
        try:
            from app.services.contract_master import get_master_contracts

            masters = await get_master_contracts(redis_conn)
            for entry in (masters or {}).values():
                if isinstance(entry, dict):
                    _add(entry.get("master"))
        except Exception:
            logger.debug("主力缓存读取失败", exc_info=True)

    # 2) kline_minute 近 3 天出现过的合约（实时固化写入面）
    if session_factory is not None:
        try:
            async with session_factory() as session:
                from sqlalchemy import text as _text

                result = await session.execute(
                    _text(
                        "SELECT DISTINCT symbol FROM kline_minute "
                        "WHERE ts >= now() - interval '3 days'"
                    )
                )
                for row in result.fetchall():
                    _add(row[0])
        except Exception:
            logger.debug("kline_minute 近期合约查询失败", exc_info=True)

    if not items:
        return _unique_codes()  # 兜底：静态合约表（每品种取代表合约）
    return list(items.values())


async def sync_kline_from_tickdata(
    session_factory: Any,
    redis_conn: aioredis.Redis | None = None,
    codes: list[dict[str, str]] | None = None,
    periods: list[str] | None = None,
    trigger: str = "manual",
) -> dict[str, int]:
    """读 pg-tickdata 权威 K线修正主 PG（两段式第二段，不直连 vvtr）

    Args:
        session_factory: AsyncSession 工厂
        redis_conn: Redis 连接（写修正状态/日志）
        codes: 品种清单（需含 symbol），None 则自动构建（主力 ∪ 近期库内合约）
        periods: 周期列表，None 则用默认（分钟全周期 + 日线）
        trigger: 触发来源（scheduled_0200/scheduled_1530/manual）

    Returns:
        {period: 写入条数} 统计
    """
    if not codes:
        codes = await _build_sync_items(redis_conn, session_factory)
    all_periods = periods or (SYNC_PERIODS_MINUTE + [SYNC_PERIOD_DAILY])

    total_codes = len(codes)
    stats: dict[str, int] = {p: 0 for p in all_periods}
    errors: list[str] = []
    start_ts = time.monotonic()
    started_at = _now_bj_iso()

    # 写初始状态：running
    await _write_sync_status(redis_conn, {
        "status": "running",
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": None,
        "duration_sec": None,
        "total_codes": total_codes,
        "processed": 0,
        "stats": stats,
        "errors": [],
        "last_run": started_at,
    })

    logger.info("权威修正开始（读 pg-tickdata）：%d 合约 × %d 周期（触发=%s）", total_codes, len(all_periods), trigger)

    for idx, item in enumerate(codes, 1):
        code = item["code"]
        symbol = str(item.get("symbol") or "").strip().lower() or _resolve_main_symbol(code)
        code_start = time.monotonic()
        code_periods: dict[str, dict[str, Any]] = {}
        code_errors: list[str] = []

        for period in all_periods:
            try:
                # 单合约单周期最多 120 秒（vvtr 1rps 节流下单次拉取数秒，留足余量）
                n, xc_stats = await asyncio.wait_for(
                    _sync_one_period(
                        session_factory, code, symbol, period, redis_conn
                    ),
                    timeout=120,
                )
                stats[period] += n
                code_periods[period] = {
                    "written": n,
                    "verified": xc_stats.get("verified", 0),
                    "single": xc_stats.get("single", 0),
                    "conflict": xc_stats.get("conflict", 0),
                    "skipped": xc_stats.get("skipped", 0),
                }
            except asyncio.TimeoutError:
                msg = f"{symbol} {period} 超时(>120s)"
                code_errors.append(msg)
                errors.append(msg)
                logger.warning(
                    "权威修正 %s %s 超时(>120s)，跳过", symbol, period
                )
                code_periods[period] = {
                    "written": 0, "verified": 0, "single": 0,
                    "conflict": 0, "skipped": 0, "error": True,
                }
            except Exception:
                msg = f"{symbol} {period} 异常"
                code_errors.append(msg)
                errors.append(msg)
                logger.warning("权威修正 %s %s 失败", symbol, period, exc_info=True)
                code_periods[period] = {
                    "written": 0, "verified": 0, "single": 0,
                    "conflict": 0, "skipped": 0, "error": True,
                }

        code_elapsed = time.monotonic() - code_start
        code_status = "error" if code_errors else "ok"

        # 写品种级日志
        log_entry = {
            "ts": _now_bj_iso(),
            "code": code,
            "symbol": symbol,
            "elapsed_sec": round(code_elapsed, 1),
            "periods": code_periods,
            "status": code_status,
        }
        if code_errors:
            log_entry["errors"] = code_errors
        await _push_sync_log(redis_conn, log_entry)

        # 更新汇总状态（processed 进度）
        await _write_sync_status(redis_conn, {
            "status": "running",
            "trigger": trigger,
            "started_at": started_at,
            "finished_at": None,
            "duration_sec": None,
            "total_codes": total_codes,
            "processed": idx,
            "stats": stats,
            "errors": errors[-20:],  # 保留最近 20 个错误
            "last_run": started_at,
        })

        logger.info(
            "权威修正 %d/%d %s（%s）%.1fs %s",
            idx, total_codes, code, symbol, code_elapsed,
            code_status,
        )

        # 品种间节流（最后一个品种不 sleep）
        if idx < total_codes:
            await asyncio.sleep(_INTER_CODE_SLEEP)

    elapsed = time.monotonic() - start_ts
    finished_at = _now_bj_iso()
    summary = ", ".join(f"{p}={n}" for p, n in stats.items())

    # 写最终状态：completed
    await _write_sync_status(redis_conn, {
        "status": "completed" if not errors else "completed_with_errors",
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": round(elapsed, 1),
        "total_codes": total_codes,
        "processed": total_codes,
        "stats": stats,
        "errors": errors[-20:],
        "last_run": started_at,
    })

    logger.info(
        "权威修正完成：%d 合约，%.0fs，%s，错误 %d 个",
        total_codes, elapsed, summary, len(errors),
    )

    # 主 PG 实时固化 1m 差集沉淀永久库（防 vvtr/HTTP 链尾段缺漏留洞）
    try:
        await backfill_tickdata_1m_from_mainpg(session_factory)
    except Exception:
        logger.warning("pg-tickdata 1m 差集补全失败", exc_info=True)
    return stats


async def _is_sync_enabled(
    redis_conn: aioredis.Redis | None,
    session_factory: Any,
) -> bool:
    """检查 K线修正开关是否开启（默认 True）"""
    try:
        async with session_factory() as session:
            from app.services.system_settings_svc import ensure_system_settings, settings_to_dict

            row = await ensure_system_settings(session)
            cfg = settings_to_dict(row)
            return bool(cfg.get("kline_sync_enabled", True))
    except Exception:
        return True  # 读取失败默认开


# 定时触发点（北京时间）：两段式第二段——第一段 tickdata 定点
# （01:00/15:05，见 vvtr_tickdata_sync._DAILY_POINTS）完成后错峰执行
SYNC_FIRE_TIMES: list[tuple[int, int, str]] = [
    (2, 0, "scheduled_0200"),
    (15, 30, "scheduled_1530"),
]


async def kline_sync_loop(
    redis_conn: aioredis.Redis,
    session_factory: Any,
) -> None:
    """定时修正循环：北京时间 02:00 + 15:30（两段式第二段，读 pg-tickdata）

    两个时间点各自独立去重（同一天同一时间点只触发一次）：
    - 02:00 凌晨修正：第一段 tickdata 同步（01:00 定点拉 vvtr）完成后 1 小时，
      夜盘主段数据的昨日兜底；
    - 15:30 闭盘修正：第一段 tickdata 同步（15:05 定点拉 vvtr，覆盖日盘
      收盘最后一段）完成后 25 分钟，客户端当晚拿到的就是权威值。

    通过 system_settings.kline_sync_enabled 开关控制（默认开）;
    管理端另可用 kline:task:kline_sync:paused 统一暂停键(两个都开才执行)。
    """
    from app.services.kline_task_status import is_paused
    from app.services.trading_hours import beijing_now

    last_fired: dict[str, str] = {}
    fire_times = SYNC_FIRE_TIMES

    await asyncio.sleep(30)
    # 启动时清理上一代进程残留的 running 状态（部署中断 sync 会卡 running）
    await _cleanup_stale_running_status(redis_conn)
    while True:
        try:
            bj = beijing_now()
            today = bj.strftime("%Y-%m-%d")
            for hh, mm, trigger_name in fire_times:
                key = f"{hh:02d}{mm:02d}"
                if (
                    bj.hour == hh
                    and bj.minute == mm
                    and last_fired.get(key) != today
                ):
                    last_fired[key] = today
                    # 检查开关：关闭时跳过（system_settings + 统一暂停键）
                    if await is_paused(redis_conn, "kline_sync"):
                        logger.info("K线修正已暂停(管理端)，跳过 %02d:%02d 触发", hh, mm)
                        continue
                    if not await _is_sync_enabled(redis_conn, session_factory):
                        logger.info("K线修正已关闭，跳过 %02d:%02d 触发", hh, mm)
                        continue
                    logger.info("vvtr 定时修正触发 %02d:%02d（北京时间）", hh, mm)
                    try:
                        await sync_kline_from_tickdata(
                            session_factory, redis_conn, trigger=trigger_name
                        )
                    except Exception:
                        logger.exception("vvtr 定时修正异常")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("kline_sync_loop 异常")
        await asyncio.sleep(60)
