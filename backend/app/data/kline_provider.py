"""K 线历史多数据源调度 —— 主备模式 + 健康冷却

把历史 K 线从「唯一新浪」扩展为多源（新浪 / TqSdk / 东财），
采用「主渠道 + 备用渠道」模型：

- K 线全部优先走主渠道；主渠道失败（空数据/异常）或处于冷却中时，
  按备用渠道配置顺序依次降级，直到取到数据；
- remote 源（远端中转）若配置了 base 地址，始终作为最后兜底。

健康冷却：某源连续失败 >= FAIL_THRESHOLD 次进入 COOLDOWN_SECONDS 冷却，
期间跳过该源；成功后清零。防止持续打已被封的源。

所有源输出统一归一化为标准 bar：
    {time, open, high, low, close, volume, settle, open_interest}
time 秒级 "YYYY-MM-DD HH:MM:SS" / 日线 "YYYY-MM-DD"（与新浪一致）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("qihuo.kline_provider")

# 合法 K 线源（tencent 接口不可用已移除；remote 由 QIHUO_KLINE_REMOTE_BASE 启用）
# simnow 从 PG 读取（VVTR 权威修正 + 实时固化的数据）——kline_sync 定时把
# VVTR 权威值写入 PG，是链内最可信的源
# vvtr 已退出客户端拉取链（限频 1 req/s 扛不住多客户端），仅由 kline_sync
# 定时修正消费；历史配置里残留的 "vvtr" 会被 normalize_sources 过滤掉
# tqsdk 已整体移除（2026-08-22）：其时间标签口径与 VVTR/交易所不一致，
# 降级供数时 K 线排列错位；残留配置同样被过滤
# eastmoney 已移除（2026-08-22）：push2his 对部分机房 IP 长期不可用且
# 数据口径与交易所存在差异，价值有限；残留配置同样被过滤
VALID_KLINE_SOURCES: list[str] = [
    "sina",
    "simnow",
    "remote",
]

# 默认主渠道（新浪覆盖全周期；个别合约脏价由 kline.get_kline_history
# 的价格一致性门拦截后降级 simnow）
DEFAULT_PRIMARY = "sina"
# 默认备用渠道（simnow=PG：VVTR 权威修正 + 实时固化数据）
DEFAULT_BACKUP = "simnow"

# 连续失败多少次进入冷却
FAIL_THRESHOLD = 3
# 冷却时长（秒）
COOLDOWN_SECONDS = 60
# 失败计数键寿命（秒）
FAILS_TTL = 300

# 健康键
_HEALTH_FAILS_PREFIX = "kline:source:fails"
_HEALTH_COOLDOWN_PREFIX = "kline:source:cooldown"

# 各源同步拉取函数（asyncio.to_thread 包装，避免阻塞事件循环）
# 签名统一：fetch_bars(symbol, period) -> list[dict]
_SOURCE_FETCHERS: dict[str, Any] = {}


def normalize_sources(order: str | None) -> list[str]:
    """解析源列表配置串，过滤非法源并去重（保序）

    返回 [primary, *backups] 形式；空输入回退默认主备（东财→新浪）。
    历史配置中的 tqsdk/vvtr 已从合法源移除，会被此处静默过滤。
    """
    if not order:
        return [DEFAULT_PRIMARY] + DEFAULT_BACKUP.split(",")
    out: list[str] = []
    for name in str(order).split(","):
        name = name.strip().lower()
        if name in VALID_KLINE_SOURCES and name not in out:
            out.append(name)
    return out or ([DEFAULT_PRIMARY] + DEFAULT_BACKUP.split(","))


# ========== 各源适配 ==========


def _normalize_bar(bar: dict[str, Any]) -> dict[str, Any]:
    """补全标准字段，缺失置 None"""
    return {
        "time": str(bar.get("time") or ""),
        "open": float(bar.get("open") or 0),
        "high": float(bar.get("high") or 0),
        "low": float(bar.get("low") or 0),
        "close": float(bar.get("close") or 0),
        "volume": int(float(bar.get("volume") or 0)),
        "settle": bar.get("settle"),
        "open_interest": bar.get("open_interest"),
    }


def _valid_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤无效 bar 并归一化"""
    out: list[dict[str, Any]] = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        try:
            if not (float(b.get("open") or 0) > 0 and float(b.get("close") or 0) > 0):
                continue
        except (TypeError, ValueError):
            continue
        nb = _normalize_bar(b)
        if nb["time"]:
            out.append(nb)
    return out


def _fetch_sina(symbol: str, period: str) -> list[dict[str, Any]]:
    """新浪源（经 qihuo-sina-bridge HTTP 代理，本进程不出网）"""
    from app.connectors.sina.client import (
        fetch_daily_kline_via_bridge,
        fetch_minute_kline_via_bridge,
    )

    if period == "1d":
        return _valid_bars(fetch_daily_kline_via_bridge(symbol))
    return _valid_bars(fetch_minute_kline_via_bridge(symbol, period))


def _code_exchange_for_symbol(symbol: str) -> tuple[str, str]:
    """合约代码 → (品种 code 小写, 交易所中文名)"""
    from app.data.contracts import ALL_CONTRACTS, get_code_for_symbol

    code = get_code_for_symbol(symbol).lower()
    exchange = ""
    if code:
        for c in ALL_CONTRACTS:
            if str(c.get("code") or "").lower() == code:
                exchange = str(c.get("exchange") or "")
                break
    return code, exchange


def _fetch_simnow(symbol: str, period: str) -> list[dict[str, Any]]:
    """SimNow 源：从 PG 读取历史 K线（本地自产数据）

    数据来源：
    - TqSdk 定时修正（09:10 + 15:30）写入 kline_minute / kline_history
    - SimNow 实时 tick 聚合写入 kline_minute（PR#4）

    同步函数（被 asyncio.to_thread 调用在独立线程中执行），内部用 asyncio.run()
    创建独立事件循环 + asyncpg 独立连接查询 PG，不依赖主进程的 session_factory
    （避免跨事件循环使用 asyncpg 连接池的 bug）。
    """
    try:
        import asyncio

        return asyncio.run(_fetch_simnow_async(symbol, period))
    except RuntimeError:
        # 已在事件循环内（不应发生，to_thread 是独立线程），兜底返回空
        return []
    except Exception:
        logger.debug("SimNow(PG) K线查询失败 %s %s", symbol, period, exc_info=True)
        return []


async def _fetch_simnow_async(
    symbol: str,
    period: str,
) -> list[dict[str, Any]]:
    """从 PG 读取 K线（用 asyncpg 独立连接，不依赖主进程 session_factory）"""
    from datetime import datetime, timezone, timedelta

    import asyncpg

    from app.data.contracts import get_code_for_symbol
    from app.data.sina_kline import aggregate_from_1m

    _BJ_TZ = timezone(timedelta(hours=8))
    code = get_code_for_symbol(symbol)
    if not code:
        return []

    # 从配置获取 PG 连接参数
    try:
        from app.core.config import load_settings

        s = load_settings()
        pg_host = s.postgres_host
        pg_port = s.postgres_port
        pg_user = s.postgres_user
        pg_password = s.postgres_password
        pg_db = s.postgres_db
    except Exception:
        logger.debug("SimNow(PG) 配置读取失败", exc_info=True)
        return []

    conn = None
    try:
        conn = await asyncpg.connect(
            host=pg_host,
            port=pg_port,
            user=pg_user,
            password=pg_password,
            database=pg_db,
        )
        if period == "1d":
            # 日线：kline_history 表（code 可能大写或小写，ILIKE 兼容）
            # asyncpg 要求 DATE 列参数为 date 对象（不能用字符串）
            # DISTINCT ON 去重：PG 可能同时有大写和小写 code 的重复行，
            # 不去重会导致 LIMIT 配额被占满、最新日期丢失
            from datetime import date as _date

            today = datetime.now(_BJ_TZ).date()
            rows = await conn.fetch(
                "SELECT DISTINCT ON (trade_date) "
                "trade_date, open, high, low, close, volume "
                "FROM kline_history "
                "WHERE code ILIKE $1 AND trade_date <= $2 "
                "ORDER BY trade_date DESC, code LIMIT $3",
                code, today, 500,
            )
            bars = [
                {
                    "time": r["trade_date"].isoformat(),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": int(r["volume"]),
                    "settle": None,
                    "open_interest": None,
                }
                for r in reversed(rows)
            ]
            # 关闭连接后再做补全（补全用同步 HTTP fetcher）
            await conn.close()
            conn = None
            return await _fill_daily_from_http(bars, symbol, code, pg_host, pg_port,
                                                pg_user, pg_password, pg_db)

        # 分钟线：kline_minute 表（code 小写，只存 1m）
        pg_rows = await conn.fetch(
            "SELECT ts, open, high, low, close, volume, hold "
            "FROM kline_minute "
            "WHERE code = $1 AND period = '1m' "
            "ORDER BY ts DESC LIMIT $2",
            code.lower(), 8964,
        )
        if not pg_rows:
            return []
        # 转标准 bar（升序）
        pg_1m = [
            {
                "time": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": int(r["volume"]),
                "settle": None,
                "open_interest": int(r["hold"]) if r["hold"] else None,
            }
            for r in reversed(pg_rows)
        ]
        if period == "1m":
            return _valid_bars(pg_1m)
        return _valid_bars(aggregate_from_1m(pg_1m, period, symbol))
    except Exception:
        logger.debug("SimNow(PG) 查询失败 %s %s", symbol, period, exc_info=True)
        return []
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


async def _fill_daily_from_http(
    pg_bars: list[dict[str, Any]],
    symbol: str,
    code: str,
    pg_host: str,
    pg_port: int,
    pg_user: str,
    pg_password: str,
    pg_db: str,
) -> list[dict[str, Any]]:
    """检查 PG 日线数据完整性，不足时从 HTTP 源补全并写回 PG

    - PG 已有数据且最新日期 >= 昨天交易日 → 数据完整，直接返回
    - PG 数据过旧或缺数据 → 从新浪拉取，upsert 到 PG，返回合并后的完整数据
    """
    from datetime import datetime, timedelta, timezone

    _BJ_TZ = timezone(timedelta(hours=8))
    now = datetime.now(_BJ_TZ)

    # 判断 PG 最新 bar 是否足够新（允许 3 天容差：周末/节假日）
    pg_latest = ""
    if pg_bars:
        pg_latest = str(pg_bars[-1].get("time") or "")[:10]
    if pg_latest:
        try:
            latest_date = datetime.strptime(pg_latest, "%Y-%m-%d").date()
            if (now.date() - latest_date).days <= 3:
                # PG 数据足够新，无需补全
                return _valid_bars(pg_bars)
        except ValueError:
            pass

    # PG 数据过旧，交叉验证拉取多源数据（tqsdk 已移除，东财+新浪两源互验）
    from app.data.kline_crosscheck import cross_validate_and_merge

    http_bars, stats = await cross_validate_and_merge(symbol, "1d")

    if not http_bars:
        # 所有源都失败，返回 PG 已有数据（即使旧）
        return _valid_bars(pg_bars)

    # 补全的数据写入 PG kline_history（永久存储）
    try:
        import asyncpg

        conn = await asyncpg.connect(
            host=pg_host, port=pg_port,
            user=pg_user, password=pg_password, database=pg_db,
        )
        try:
            # upsert 日线（ON CONFLICT 更新）
            for b in http_bars:
                d = b.get("time", "")[:10]
                if not d:
                    continue
                try:
                    from datetime import date as _date

                    await conn.execute(
                        "INSERT INTO kline_history (code, trade_date, open, high, low, close, volume, hold) "
                        "VALUES ($1, $2, $3, $4, $5, $6, $7, 0) "
                        "ON CONFLICT (code, trade_date) DO UPDATE SET "
                        "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                        "close=EXCLUDED.close, volume=EXCLUDED.volume",
                        code, _date.fromisoformat(d),
                        float(b["open"]), float(b["high"]), float(b["low"]),
                        float(b["close"]), int(b.get("volume", 0) or 0),
                    )
                except Exception:
                    continue
        finally:
            await conn.close()
        logger.info(
            "SimNow 日线补全 %s：交叉验证 %d 条写入 PG（一致%d 单源%d 冲突%d）",
            symbol, len(http_bars),
            stats.get("verified", 0), stats.get("single", 0), stats.get("conflict", 0),
        )
    except Exception:
        logger.debug("SimNow 日线补全写 PG 失败 %s", symbol, exc_info=True)

    # 合并 PG + HTTP（HTTP 覆盖同日期），去重，按日期排序
    merged: dict[str, dict[str, Any]] = {}
    for b in pg_bars:
        t = str(b.get("time") or "")[:10]
        if t:
            merged[t] = b
    for b in http_bars:
        t = str(b.get("time") or "")[:10]
        if t:
            merged[t] = b
    result = sorted(merged.values(), key=lambda x: str(x.get("time") or ""))
    return _valid_bars(result[-500:])  # 最多 500 根


def _fetch_remote(
    symbol: str,
    period: str,
    remote_base: str = "",
) -> list[dict[str, Any]]:
    """远端 K 线源：从另一台服务器（如 33）的 /api/market/kline 中转。

    用于本地所有源都不可用（网络/封禁）时兜底——例如 58 到天勤 auth 不通、
    新浪被封、东财断连时，从 33（tqsdk 正常）拉取历史。
    """
    if not remote_base:
        return []
    import httpx

    url = remote_base.rstrip("/") + "/api/market/kline"
    params = {"symbol": symbol, "period": period, "limit": 1024}
    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        logger.debug("远端 K 线拉取失败 %s %s (%s)", symbol, period, remote_base)
        return []
    bars = (payload or {}).get("bars") or []
    out: list[dict[str, Any]] = []
    for b in bars:
        if not isinstance(b, dict) or not b.get("time"):
            continue
        out.append(_normalize_bar(b))
    return out


def _register_fetchers(
    remote_base: str = "",
) -> None:
    """注册各源同步拉取函数

    vvtr 不在此注册：客户端请求走的多源降级链不再打 vvtr（限频 1 req/s，
    多客户端缓存未命中会把分发源打爆）。vvtr 仅由 kline_sync 定时修正
    （01:00 + 15:05）单点消费,修正结果并入历史库与 Redis 历史缓存后
    由客户端直接读取。
    tqsdk/eastmoney 已移除（2026-08-22）：tqsdk 时间标签口径与 VVTR/交易所
    不一致导致排列错位；eastmoney 对部分机房 IP 长期不可用且口径有差。
    """
    _SOURCE_FETCHERS.clear()
    _SOURCE_FETCHERS["sina"] = _fetch_sina
    _SOURCE_FETCHERS["simnow"] = _fetch_simnow
    _SOURCE_FETCHERS["remote"] = lambda symbol, period: _fetch_remote(
        symbol, period, remote_base
    )


# ========== 健康状态（Redis） ==========


def _fails_key(source: str) -> str:
    return f"{_HEALTH_FAILS_PREFIX}:{source}"


def _cooldown_key(source: str) -> str:
    return f"{_HEALTH_COOLDOWN_PREFIX}:{source}"


async def _in_cooldown(redis_conn: aioredis.Redis | None, source: str) -> bool:
    """该源是否处于冷却中（跳过）；无 Redis 时不做健康限制"""
    if redis_conn is None:
        return False
    try:
        return bool(await redis_conn.exists(_cooldown_key(source)))
    except Exception:
        return False


async def _mark_failure(
    redis_conn: aioredis.Redis | None, source: str
) -> None:
    """源失败：计数 +1；达到阈值进入冷却"""
    if redis_conn is None:
        return
    try:
        pipe = redis_conn.pipeline()
        pipe.incr(_fails_key(source))
        pipe.expire(_fails_key(source), FAILS_TTL)
        fails, _ = await pipe.execute()
        if int(fails) >= FAIL_THRESHOLD:
            await redis_conn.set(_cooldown_key(source), "1", ex=COOLDOWN_SECONDS)
    except Exception:
        logger.exception("K 线源健康计数失败 %s", source)


async def _mark_success(
    redis_conn: aioredis.Redis | None, source: str
) -> None:
    """源成功：清零失败计数与冷却"""
    if redis_conn is None:
        return
    try:
        pipe = redis_conn.pipeline()
        pipe.delete(_fails_key(source))
        pipe.delete(_cooldown_key(source))
        await pipe.execute()
    except Exception:
        pass


# ========== 调度 ==========


async def fetch_kline_with_fallback(
    redis_conn: aioredis.Redis | None,
    symbol: str,
    period: str,
    primary: str = DEFAULT_PRIMARY,
    backups: list[str] | None = None,
    remote_base: str = "",
    is_stale: Callable[[list[dict[str, Any]]], bool] | None = None,
) -> list[dict[str, Any]]:
    """主渠道优先，失败/冷却时按备用渠道顺序降级，返回第一个成功源的 bars

    - 冷却中的源跳过；
    - 传入 is_stale 时，某源返回的 bars 过旧视为失败，继续降级下一源
      （用于「最新视图」新鲜度守卫，防止离线库旧数据冒充最新行情）；
    - 全部失败返回 []（调用方应写负缓存防高频重试）；
    - remote 源只在配置了远端地址时启用，且始终放在最后（兜底）。

    兼容旧调用：若传入 mode/sources（关键字），自动转换为 primary/backups。
    """
    # 构建尝试顺序：主渠道 → 备用渠道（去重、过滤非法）
    primary = (primary or DEFAULT_PRIMARY).strip().lower() or DEFAULT_PRIMARY
    backup_list = backups if backups is not None else DEFAULT_BACKUP.split(",")
    seen: set[str] = set()
    order: list[str] = []
    for src in [primary, *backup_list]:
        src = (src or "").strip().lower()
        if src and src in VALID_KLINE_SOURCES and src not in seen:
            seen.add(src)
            order.append(src)
    if not order:
        order = [DEFAULT_PRIMARY] + DEFAULT_BACKUP.split(",")
    # remote 源只在配置了远端地址时启用，且始终放在最后（兜底）
    if remote_base and "remote" not in order:
        order = order + ["remote"]

    _register_fetchers(remote_base)

    for source in order:
        fetcher = _SOURCE_FETCHERS.get(source)
        if fetcher is None:
            continue
        if await _in_cooldown(redis_conn, source):
            logger.info("K 线源 %s 冷却中，跳过 %s %s", source, symbol, period)
            continue
        try:
            bars = await asyncio.to_thread(fetcher, symbol, period)
        except Exception:
            # 异常=源故障（连接/HTTP/解析失败），保留失败计数语义
            logger.exception("K 线源 %s 拉取异常 %s %s", source, symbol, period)
            await _mark_failure(redis_conn, source)
            continue
        if bars:
            if is_stale is not None and is_stale(bars):
                await _mark_failure(redis_conn, source)
                logger.warning(
                    "K 线源 %s 数据过期（最新 %s），跳过换下一源 %s %s",
                    source, bars[-1].get("time"), symbol, period,
                )
                continue
            await _mark_success(redis_conn, source)
            # VVTR 源写通 pg-tickdata（fire-and-forget，60s 节流，失败不影响返回）
            if source == "vvtr":
                try:
                    from app.data.tickdata_writer import persist_bars_task

                    persist_bars_task(symbol, period, bars)
                except Exception:
                    logger.debug("TickData 写通调度失败 %s %s", symbol, period)
            return bars
        # 空=该合约在此源无数据（合约属性，非源故障）：不计源失败——
        # 否则 3 个死合约就能把唯一源打进 60s 冷却=全断
        # （2026-08-25 ma2609 分钟空数据事件根因）
        logger.warning(
            "K 线源 %s 无数据（不计源失败），降级下一源 %s %s",
            source, symbol, period,
        )

    return []
