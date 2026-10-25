"""K 线服务层 —— 历史数据缓存 + 实时 bar 更新

从新浪 API 拉取 K 线历史数据并缓存到 Redis，
基于行情数据实时更新最新 K 线 bar。
"""

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import date, datetime, timezone, timedelta
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.contracts import get_code_for_symbol
from app.data.product_specs import extract_product_code
from app.data.sina_kline import aggregate_from_1m
from app.data.tickdata_kline import fetch_tickdata_kline
from app.services.kline_history import query_history_bars
from app.services.kline_minute import query_minute_history
from app.services.session_profiles import (
    all_sessions,
    fold_tick_minute_edge,
    get_profile,
    period_end_for_product,
    period_start_for_product,
)
from app.services.trading_hours import (
    get_trading_day,
    is_trading_hours,
    is_symbol_trading,
)

# 北京时间（UTC+8）
_BJ_TZ = timezone(timedelta(hours=8))

logger = logging.getLogger("qihuo.kline")

# Redis 缓存 key 前缀
_HISTORY_KEY_PREFIX = "kline:history"
_REALTIME_KEY_PREFIX = "kline:realtime"

# 历史 K 线 TTL（秒）—— 仅缓存「已收盘」序列，加速切品种/切分时。
# 收盘 bar 由 append_closed_bar 在收线时主动固化进该缓存、最新价由
# forming 合并补齐，TTL 只控制对账节奏：分钟级给 10 分钟，避免
# 3 分钟一过期就撞 vvtr 冷路径（三窗口×1rps 节流 ≈5s）。
_TTL_TRADING_MINUTE = 600  # 交易时段分钟级：10 分钟
_TTL_TRADING_DAILY = 300  # 交易时段日线：5 分钟
_TTL_IDLE = 900  # 非交易时段：15 分钟

# 实时 K 线状态需跨越午休，以及 02:30 夜盘到 09:00 日盘的最长间隔。
# 长 TTL 不等于允许陈旧数据：读取和更新时仍会严格校验当前官方周期键。
_TTL_REALTIME = 12 * 60 * 60

# 全源失败负缓存：AI 交易(2s)/因子挖掘(1s)循环在缓存 miss 时会高频重试，
# 若所有数据源都失败且不缓存，会持续重打数据源（正是新浪被封的放大因素）。
# 全源失败后写短 TTL 负缓存，期间直接返回空，不再重试。
_FAIL_CACHE_PREFIX = "kline:history:fail"
_FAIL_CACHE_TTL = 30

# 最近 N 天回溯请求走分发源主链（vvtr 主源），不直连 TickData：
# 库内非主力合约缺自身数据（只有跨合约拼接段），最近月必须来自分发源，
# 与 tickdata_kline._STITCH_MIN_AGE_DAYS 保持一致。
# 例外（2026-08-23）：分钟多周期（5/15/30/60m）不受此限——新浪原生多分钟
# 停用后主链只能 1m 聚合（~1023 根 → 60m 仅 ~17 根，低于因子实验室客户端
# 60 根门槛）。fetch_tickdata_kline 对 <30 天锚点只回本合约自身 bar
# （stitch_allowed=False，无 ma2701 式跨合约污染），且带新鲜度守卫拦滞后。
_RECENT_DIST_DAYS = 30

# 分钟深历史补全：多分钟周期（新浪 1m 聚合根数受 1m 窗口限制）最新视图
# 根数不足时，从 pg-tickdata 取本合约更早段补前缀。目标根数与客户端
# 因子实验室每页 500 对齐；请求 limit 更大时按 limit 取。
_MINUTE_DEEP_PERIODS = ("5m", "15m", "30m", "60m")
_MINUTE_DEEP_TARGET = 500

# forming 分桶允许的交易所时钟与本地钟最大偏差：超过视为脏时间戳
# （源时钟异常/解析错位），回退本地钟，防止 tick 归错桶。
_EXCHANGE_CLOCK_MAX_DRIFT = timedelta(minutes=5)

# 支持的 K 线周期（不含 tick）
KLINE_PERIODS: list[str] = ["1m", "5m", "15m", "30m", "60m", "1d"]

# 每个合约每个周期的当前 bar 状态
# 结构：{symbol: {period: {"bar": {...}, "period_start_volume": int}}}
_realtime_state: dict[str, dict[str, dict[str, Any]]] = {}


def _history_cache_key(symbol: str, period: str) -> str:
    """生成历史 K 线缓存 key"""
    return f"{_HISTORY_KEY_PREFIX}:{symbol}:{period}"


def _realtime_cache_key(symbol: str, period: str) -> str:
    """生成实时 K 线缓存 key"""
    return f"{_REALTIME_KEY_PREFIX}:{symbol}:{period}"


def _normalize_bar(bar: dict[str, Any]) -> dict[str, Any]:
    """单条 bar 字段补全：确保 settle / open_interest 字段始终存在

    用于兼容旧版 Redis 缓存（缺字段时返回 None），
    保证 API 返回结构稳定，避免新前端读到 undefined。
    """
    if "settle" not in bar:
        bar["settle"] = None
    if "open_interest" not in bar:
        bar["open_interest"] = None
    return bar


def _bar_time_key(period: str, time_str: str) -> str:
    """统一 bar 时间键：日线只比日期，分钟线比完整时间串"""
    t = str(time_str or "").strip()
    if not t:
        return ""
    if period == "1d":
        return t[:10]
    return t


def _dedupe_bars_by_time(
    bars: list[dict[str, Any]],
    period: str,
) -> list[dict[str, Any]]:
    """按时间键去重，同键后者覆盖前者（实时覆盖历史）

    避免日线同时出现 `YYYY-MM-DD` 与 `YYYY-MM-DD 00:00:00` 导致
    前端 lightweight-charts setData 因重复 time 失败。
    """
    if not bars:
        return bars
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for bar in bars:
        key = _bar_time_key(period, str(bar.get("time") or ""))
        if not key:
            continue
        if key not in by_key:
            order.append(key)
        by_key[key] = bar
    return [by_key[k] for k in order]


def _normalize_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量补全 bar 字段"""
    for bar in bars:
        _normalize_bar(bar)
    return bars


def _get_ttl(period: str) -> int:
    """根据周期和交易时段获取缓存 TTL"""
    trading = is_trading_hours()
    if not trading:
        return _TTL_IDLE
    return _TTL_TRADING_DAILY if period == "1d" else _TTL_TRADING_MINUTE


def _latest_max_age(period: str) -> timedelta | None:
    """最新视图新鲜度阈值：分钟 2×周期+15；日线 4 个自然日"""
    if period == "1d":
        return timedelta(days=4)
    minutes = _PERIOD_MINUTES.get(period)
    if minutes is None:
        return None
    return timedelta(minutes=2 * minutes + 15)


def _daily_closed_floor(now_bj: datetime) -> date | None:
    """夜盘时段应已闭合的日线交易日下限（品种维度由调用方 gate 保证）。

    夜盘开盘中（21:00 后），当日白盘已收盘 → 闭合日线应含今天（自然日）；
    跨午夜夜盘（次日 00:00-02:30）应含昨日自然日。白盘时段与日间休市
    不设下限（"上一交易日"依赖交易日历，维持 4 天宽限口径）。
    2026-08-24 夜：新浪日线滞后到 21:1x 才更新 8-24 闭合 bar，4 天宽限
    放行尾部 8-21 的序列并覆盖缓存 → 图上 8-24 日线消失，即此缺口。
    """
    from datetime import time as _dtime

    t = now_bj.time()
    if t >= _dtime(20, 45):
        return now_bj.date()
    if t <= _dtime(3, 0):
        return now_bj.date() - timedelta(days=1)
    return None


def _daily_floor_missing(
    bars: list[dict[str, Any]], now_bj: datetime | None = None
) -> bool:
    """夜盘时段日线序列尾部早于「当日已闭合交易日」→ 判缺当日闭合 bar"""
    floor = _daily_closed_floor(now_bj or datetime.now(_BJ_TZ))
    if floor is None or not bars:
        return False
    dt = _parse_bar_clock(str(bars[-1].get("time") or ""))
    if dt is None:
        return False
    return dt.date() < floor


async def _forming_bar_fresh(
    redis_conn: aioredis.Redis,
    trading_mode: str,
    symbol: str,
    period: str,
    max_age: timedelta,
) -> bool:
    """实时 forming bar 是否新鲜（时间键为周期起点，按起点判龄）"""
    from app.services.kline_keys import realtime_kline_key

    try:
        raw = await redis_conn.get(realtime_kline_key(trading_mode, symbol, period))
        if not raw:
            return False
        payload = json.loads(raw)
        rt = payload.get("bar") if isinstance(payload, dict) else None
        dt = _parse_bar_clock(str((rt or {}).get("time") or ""))
        if dt is None:
            return False
        return datetime.now(_BJ_TZ).replace(tzinfo=None) - dt <= max_age
    except Exception:
        return False


def _build_latest_stale_gate(
    period: str,
    symbol: str,
) -> Callable[[list[dict[str, Any]]], bool] | None:
    """构造「最新视图」新鲜度守卫；非交易时段返回 None（不拦截）。

    背景：交易时段内切换品种时，若实时源（东财/新浪/tqsdk）慢或全挂，
    PG/TickData 等离线兜底会返回几天前的旧 K 线，被画到图上并喂给
    AI 交易引擎 → AI 把过期行情当最新做判断、错误下单。

    规则：仅当该品种当前在交易时拦截——
    - 分钟线：最新 bar 距今超过 2×周期+15 分钟视为过期；
    - 日线：最新 bar 距今超过 4 个自然日视为过期（覆盖周五数据
      在周一/周二盘中仍可用的周末间隙）。
    周末/节假日/品种休市时不拦截：上一交易段的数据本就是最新，
    且此时 AI 任务不交易，无误导风险。
    """
    now = datetime.now(_BJ_TZ)
    if not is_trading_hours() or not is_symbol_trading(symbol, now):
        return None
    max_age = _latest_max_age(period)
    if max_age is None:
        return None

    def _is_stale(bars: list[dict[str, Any]]) -> bool:
        if not bars:
            return False
        if period == "1d" and _daily_floor_missing(bars):
            # 夜盘时段缺当日闭合日线（源滞后）：不因 4 天宽限放行，
            # 拒掉后降级到含当日的源 / tickdata，防止旧序列覆盖缓存
            return True
        dt = _parse_bar_clock(str(bars[-1].get("time") or ""))
        if dt is None:
            # 时间无法解析：不拦截（防御性，避免误杀）
            return False
        age = datetime.now(_BJ_TZ).replace(tzinfo=None) - dt
        return age > max_age

    return _is_stale


async def _live_ref_price(
    redis_conn: aioredis.Redis, symbol: str
) -> tuple[float, datetime | None]:
    """取 live 最新价作跨源价格基准（>0 有效；失败返回 0 = 不启用价格门）。

    同时返回基准的行情时间，供价格门判断「基准与候选 bar 是否同段」
    ——基准远新于 bar 时（剧烈行情/基准滞后）跳过比价防误杀。
    基准优先级：
    1. quote 主键（scheduler 每秒刷新）；
    2. bridge 源键（TTL 7 天）；
    3. pg-tickdata 最新收盘（VVTR 定时同步的权威库）——redis 重启/周末
       前两级全空时的兜底，保证价格门任何时候都有弹药（2026-08-22
       周六 redis 重建后前两级缺失，新浪脏价趁虚而入的教训）。
    """
    sym = str(symbol or "").strip().lower()
    for key in (
        f"market:live:quote:{sym}",
        f"market:live:source:vvtr:quote:{sym}",
        f"market:live:source:real_ctp:quote:{sym}",
    ):
        try:
            raw = await redis_conn.get(key)
        except Exception:
            continue
        if not raw:
            continue
        try:
            data = json.loads(raw)
            last = float(data.get("last_price") or 0)
        except Exception:
            continue
        if last > 0:
            # tick_time 有 17 位数字(VVTR)/HH:MM:SS/iso 多形态,直接
            # _parse_bar_clock 解不了 17 位 → ref_dt=None,价格门的
            # 「基准远新于 bar 放行」分支从未生效(2026-08-25 夜盘
            # ma2701 真实暴跌 2.3% 被当脏价全源拒掉的根因)。
            # 优先取已归一的 tick_time_iso,缺失再现场归一。
            from app.data.quote_schema import normalize_tick_time_iso

            iso = data.get("tick_time_iso") or normalize_tick_time_iso(
                data.get("tick_time")
            )
            ref_dt = _parse_bar_clock(str(iso)) if iso else None
            return last, ref_dt
    # 兜底：pg-tickdata 权威最新收盘（本地库查询，零外部请求）
    try:
        td = await fetch_tickdata_kline(sym, "1m", end_time="", limit=1)
        if td:
            last = float(td[-1].get("close") or 0)
            if last > 0:
                ref_dt = _parse_bar_clock(str(td[-1].get("time") or ""))
                return last, ref_dt
    except Exception:
        logger.debug("tickdata 基准价读取失败 %s", sym, exc_info=True)
    return 0.0, None


def _build_price_gate(
    ref_price: float,
    ref_dt: datetime | None,
    period: str,
) -> Callable[[list[dict[str, Any]]], bool]:
    """价格一致性门：最新 close 偏离实时基准超 2% 视为脏源。

    实测 2026-08-22：58 东财不通降级新浪，新浪对 au2612 返回滞后价，
    与权威差 2.5%，图面出现 24 元断崖。跨源同时刻价格差正常 <0.5%，
    2% 阈值只拦脏源不误杀（涨跌停时 bar close 与 quote 同步，不受影响）。
    基准 quote 远新于候选 bar（>3×周期）时跳过——剧烈行情里老 bar 与
    现价的偏差是真实的，不是脏数据。
    日线（period=1d）bar 时间只有日期：比价基准取「当日 15:00 收盘」
    ——夜盘时段昨收 bar 与现价的隔夜真实偏差（2026-08-25 ma2701 夜盘
    -2.3%）按 >3h 放行；盘中同日 bar（收盘前）不享受放行，脏价照拦。
    """
    period_minutes = _PERIOD_MINUTES.get(period, 60)
    max_lead = timedelta(minutes=3 * max(period_minutes, 1))
    is_daily = period == "1d"

    def _price_stale(bars: list[dict[str, Any]]) -> bool:
        if not bars or ref_price <= 0:
            return False
        try:
            close = float(bars[-1].get("close") or 0)
        except (TypeError, ValueError):
            return False
        if close <= 0 or abs(close - ref_price) / ref_price <= 0.02:
            return False
        if ref_dt is not None:
            bar_dt = _parse_bar_clock(str(bars[-1].get("time") or ""))
            if bar_dt is not None:
                if is_daily:
                    bar_dt = bar_dt.replace(hour=15, minute=0, second=0)
                if ref_dt.replace(tzinfo=None) - bar_dt > max_lead:
                    return False  # 基准远新于 bar：偏差来自行情推进，放行
        return True

    return _price_stale


# ========== 历史数据 ==========


async def get_kline_history(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    limit: int = 0,
    end_time: str = "",
    session: AsyncSession | None = None,
    trading_mode: str = "live",
    adjusted: bool = False,
) -> list[dict[str, Any]]:
    """获取 K 线历史数据，缓存优先

    1. 先从 Redis 缓存读取
    2. 缓存不存在则从新浪 API 拉取并写入缓存
    3. 新浪 API 失败则返回缓存中的过期数据（若有）
    4. 可选 limit/end_time 参数实现懒加载切片
    5. 日线模式下支持 PG 历史数据补充
    6. trading_mode=virtual 时走独立历史库 kline:virtual:history
    """
    from app.core.trading_mode import MODE_VIRTUAL, normalize_trading_mode

    # 历史回溯优先走 TickData（pg-tickdata，2005 起全周期无限回溯）
    # 放在 virtual/live 分支之前：模拟盘(virtual)的分钟线 HTTP seed 只有 ~1-2 月，
    # 拖动到更早会空；这里统一让 live/virtual 的 end_time 回溯都走 tickdata。
    # 命中直接返回（历史段无需合并 forming）；未配置/失败返回空 → 落到下面各分支。
    # 例外——最近 _RECENT_DIST_DAYS 天内的回溯不走 tickdata 直连：vvtr→tickdata
    # 定时同步只覆盖主力合约，非主力在库里只有 9999 主力拼接段（跨合约价格，
    # 2026-08-21 ma2701 15m 污染路径），最近月一律走分发源主链，更早才用库。
    # 分钟多周期（_MINUTE_DEEP_PERIODS）例外之例外：近端回溯也直连——
    # 新浪原生多分钟停用后主链 1m 聚合只有 ~17 根（60m），而近端锚点下
    # tickdata 只回本合约自身 bar（不拼接）+ 新鲜度守卫，无污染路径。
    _end_dt = _parse_bar_clock(end_time) if end_time else None
    _recent_cutoff = datetime.now(_BJ_TZ).replace(tzinfo=None) - timedelta(
        days=_RECENT_DIST_DAYS
    )
    if end_time and (
        _end_dt is None
        or _end_dt < _recent_cutoff
        or period in _MINUTE_DEEP_PERIODS
    ):
        td_bars = await fetch_tickdata_kline(
            symbol, period, end_time=end_time, limit=limit or 200,
            adjusted=adjusted,
        )
        if td_bars:
            td_bars = _filter_bars_not_future(td_bars, period, symbol)
            return _dedupe_bars_by_time(td_bars, period)

    if normalize_trading_mode(trading_mode) == MODE_VIRTUAL:
        from app.services.kline_virtual_hist import get_virtual_kline_history

        return await get_virtual_kline_history(
            redis_conn, symbol, period, limit=limit, end_time=end_time
        )

    cache_key = _history_cache_key(symbol, period)
    fail_key = f"{_FAIL_CACHE_PREFIX}:{symbol}:{period}"

    # 最新视图（end_time 空 = 切品种首屏 / AI 决策取数）新鲜度守卫：
    # 交易时段内任何来源的最新 bar 过旧都视为未命中，绝不把离线库的
    # 旧 K 线当最新行情返回（AI 会基于过期数据误判下单）。
    # 历史回溯请求（end_time 非空）面向过去时段，不做拦截。
    stale_gate = None if end_time else _build_latest_stale_gate(period, symbol)

    # 价格一致性门（仅 live 最新视图）：与实时 quote 比价，偏差>2% 的
    # 源视为脏源。独立于时间守卫（周末也生效）、forming 新鲜也不豁免——
    # 历史 尾部脏价会在图上拼出断崖（2026-08-22 au2612 实测）。
    from app.core.trading_mode import MODE_LIVE as _MODE_LIVE

    price_gate = None
    if not end_time and normalize_trading_mode(trading_mode) == _MODE_LIVE:
        ref_price, ref_dt = await _live_ref_price(redis_conn, symbol)
        if ref_price > 0:
            price_gate = _build_price_gate(ref_price, ref_dt, period)

    def _reject(bars: list[dict[str, Any]]) -> bool:
        if price_gate is not None and price_gate(bars):
            return True
        return stale_gate is not None and stale_gate(bars)

    # forming 放宽：实时 forming bar 新鲜时（每秒由 scheduler 更新），
    # 源历史尾部略滞后（如 vvtr 上游延迟 1~2 根已收 bar）也能在合并
    # forming 后构成当前视图。放宽为 3×宽限的有限窗口而非完全放开——
    # 完全放开会让「尾部滞后数周」的残缺长序列（2026-08-24 pp2701
    # 日线尾停在 08-10）在 forming 掩护下通过并覆盖缓存。
    if stale_gate is not None:
        _ma = _latest_max_age(period)
        if _ma is not None and await _forming_bar_fresh(
            redis_conn, trading_mode, symbol, period, _ma
        ):
            relaxed_max_age = _ma * 3

            def _is_stale_relaxed(
                bars: list[dict[str, Any]],
                _relaxed: timedelta = relaxed_max_age,
            ) -> bool:
                # 缺当日闭合日线不受 forming 放宽掩护（2026-08-24 夜
                # 8-24 日线缺口：forming 8-25 新鲜 + 源尾部 8-21 被放行）
                if _daily_floor_missing(bars):
                    return True
                if not bars:
                    return False
                dt = _parse_bar_clock(str(bars[-1].get("time") or ""))
                if dt is None:
                    return False
                age = datetime.now(_BJ_TZ).replace(tzinfo=None) - dt
                return age > _relaxed

            stale_gate = _is_stale_relaxed

    # 尝试读取缓存（缓存优先；缓存过期或负缓存只在无可用缓存时拦截，
    # 避免源恢复并写入缓存后仍被负缓存挡 30s 空转）
    cached = await redis_conn.get(cache_key)
    cached_bars: list[dict[str, Any]] | None = None
    if cached is not None:
        loaded = _filter_non_trading_bars(
            _normalize_bars(json.loads(cached)), period
        )
        if _reject(loaded):
            logger.warning(
                "历史 K 线缓存过期/脏价（最新 %s），忽略并重拉 %s %s",
                loaded[-1].get("time") if loaded else "", symbol, period,
            )
        else:
            cached_bars = loaded

    if cached_bars is not None:
        bars = cached_bars
    else:
        # 负缓存：全源失败后短 TTL 内直接返回空，不重试
        try:
            if await redis_conn.get(fail_key):
                return []
        except Exception:
            pass
        bars: list[dict[str, Any]] = []
        # 闭市时段优先 pg-tickdata 永久历史（仅最新视图；历史回溯
        # end_time 非空走顶部 tickdata 直连/主链）。开盘中保持 HTTP
        # 链优先（实时性），tickdata 仅作兜底。
        if not end_time and not is_trading_hours():
            bars = await _tickdata_first_when_closed(
                symbol, period, limit or 500
            )
            if bars and _reject(bars):
                bars = []
        # 多数据源拉取（主备/轮询 + 失败降级 + 健康冷却；
        # 交易时段内某源返回过期数据/脏价时跳过它继续降级到下一源）
        if not bars:
            bars = await _fetch_from_provider(
                redis_conn,
                symbol,
                period,
                session,
                is_stale=_reject if (stale_gate is not None or price_gate is not None) else None,
            )
        if bars and _reject(bars):
            # 双保险：provider 内部已逐源拦截，这里防止任何路径漏网
            logger.warning(
                "实时源数据过期/脏价（最新 %s close=%s ref=%s），弃用 %s %s",
                bars[-1].get("time"), bars[-1].get("close"),
                "有" if price_gate else "无", symbol, period,
            )
            bars = []
        if not bars and session is not None:
            # PG 历史兜底（日线 kline_history / 分钟 kline_minute，
            # 由 tqsdk/akshare 离线导入）；交易时段内数据过旧/脏价则弃用
            bars = await _fetch_from_pg(session, symbol, period)
            if bars and _reject(bars):
                logger.warning(
                    "PG 历史兜底数据过期/脏价（最新 %s），弃用 %s %s",
                    bars[-1].get("time"), symbol, period,
                )
                bars = []
        # TickData 最近段兜底：仅当 HTTP/PG 全挂且（交易时段内）数据足够新
        if not bars:
            td_bars = await fetch_tickdata_kline(
                symbol, period, end_time="", limit=limit or 500
            )
            if td_bars and _reject(td_bars):
                logger.warning(
                    "TickData 兜底数据过期/脏价（最新 %s），弃用 %s %s",
                    td_bars[-1].get("time"), symbol, period,
                )
                td_bars = []
            bars = td_bars
        # 源历史可能自带周末幽灵 bar（vvtr 上游周末重放报价聚合）：
        # 入缓存前统一按交易日历过滤
        bars = _filter_non_trading_bars(bars, period)
        if bars:
            ttl = _get_ttl(period)
            await redis_conn.set(
                cache_key,
                json.dumps(bars, ensure_ascii=False),
                ex=ttl,
            )
        else:
            # 全源失败：写负缓存防高频任务雪崩重试
            try:
                await redis_conn.set(fail_key, "1", ex=_FAIL_CACHE_TTL)
            except Exception:
                pass
            return []

    # 分钟深历史补全（2026-08-23）：新浪原生多分钟停用后，主链多分钟只能
    # 由 ~1023 根 1m 聚合（60m≈17 / 30m≈34 / 15m≈68 根），低于因子实验室
    # 客户端 60 根门槛。最新视图根数不足时，从 pg-tickdata 取本合约更早段
    # 补前缀（end_time=主链最早 bar → 近端锚点，fetch_tickdata_kline 不做
    # 跨合约拼接、带新鲜度守卫；尾部实时性仍由主链 + forming 保证）。
    # 未配置 / 无该合约数据返回空，保持原序列。缓存命中的浅序列同样补全，
    # 补全后整体写回历史缓存，TTL 内后续请求直接命中完整序列。
    if (
        not end_time
        and period in _MINUTE_DEEP_PERIODS
        and bars
        and len(bars) < max(_MINUTE_DEEP_TARGET, limit or 0)
    ):
        _gap_end = str(bars[0].get("time") or "")
        if _gap_end:
            _want = max(_MINUTE_DEEP_TARGET, limit or 0)
            try:
                td_deep = await fetch_tickdata_kline(
                    symbol, period, end_time=_gap_end, limit=_want
                )
            except Exception:
                # best-effort：补深任何意外（未配置/上游异常）不影响主链返回
                td_deep = []
            if td_deep:
                bars = _dedupe_bars_by_time(td_deep + bars, period)
                try:
                    await redis_conn.set(
                        cache_key,
                        json.dumps(bars, ensure_ascii=False),
                        ex=_get_ttl(period),
                    )
                except Exception:
                    logger.debug("分钟深历史补全写缓存失败 %s %s", symbol, period)

    # 日线懒加载：end_time 超出缓存范围时从 PG 补充历史数据
    if end_time and session is not None and period == "1d":
        return await _merge_daily_history(bars, symbol, end_time, limit, session)

    # 分钟线懒加载：end_time 超出新浪缓存范围时从 PG(tqsdk导入) 补充历史
    # 让回测/因子挖掘/超级因子的分钟周期能覆盖约 6 个月（免费版 8964 根上限）
    if end_time and session is not None and period in ("1m", "5m", "15m", "30m", "60m"):
        return await _merge_minute_history(bars, symbol, period, end_time, limit, session)

    # 分钟级或初始加载：标准切片逻辑
    if end_time:
        bars = [b for b in bars if b.get("time", "") < end_time]
    if limit > 0:
        bars = bars[-limit:]
    bars = _normalize_bars(bars)

    # 去掉未来幽灵 bar（错误交易日标签等）
    bars = _filter_bars_not_future(bars, period, symbol)

    # 非历史切片请求：合并当前未收盘 bar（每秒由 scheduler 写入）
    # 这样前端用长 TTL 历史缓存时，首次绘制仍带最新价
    if not end_time:
        bars = await _attach_realtime_bar(
            redis_conn, symbol, period, bars, trading_mode
        )
    # 统一去重（日线历史与 realtime 时间格式可能不同）
    return _dedupe_bars_by_time(bars, period)


def _parse_bar_clock(time_str: str) -> datetime | None:
    """解析 bar 时间串为北京时间 naive datetime"""
    t = str(time_str or "").strip()
    if not t:
        return None
    try:
        if len(t) >= 19:
            return datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
        if len(t) >= 16:
            return datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
        if len(t) >= 10:
            return datetime.strptime(t[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return None


def _is_minute_bar_in_session(time_str: str) -> bool:
    """分钟 bar 时间是否落在粗粒度交易时段且日历有效

    时段：09:00-11:30 / 13:00-15:15 / 21:00-02:30（跨午夜）。
    日历：夜盘只存在于周一~周五晚间；周五夜盘尾巴延续到周六凌晨；
    周日全天与周一凌晨无交易（周日晚没有夜盘）。
    旧逻辑只看时刻不看星期 → 周日 21:00-23:00 的幽灵 bar 全部放行。
    """
    from datetime import time as dtime

    dt = _parse_bar_clock(time_str)
    if dt is None:
        return False
    t = dt.time()
    weekday = dt.weekday()  # bar 时间即北京墙钟

    if weekday == 6:  # 周日全天
        return False
    if weekday == 5:  # 周六：仅凌晨夜盘尾巴
        return t <= dtime(2, 30)
    if weekday == 0 and t <= dtime(2, 30):  # 周一凌晨：无夜盘尾巴
        return False

    # 日盘
    if dtime(9, 0) <= t <= dtime(11, 30):
        return True
    if dtime(13, 0) <= t <= dtime(15, 15):
        return True
    # 夜盘（含跨午夜尾巴，周二~周五凌晨有效）
    if t >= dtime(21, 0) or t <= dtime(2, 30):
        return True
    return False


def _closed_market_stale(
    bars: list[dict[str, Any]], period: str, now: datetime | None = None
) -> bool:
    """闭市时段判断永久历史尾部是否够新（最近收盘宽限）。

    闭市时合法尾端 = 最近一个已收盘时段：
    - 日线宽限 5 个自然日（覆盖周末 + 小长假 + 同步延迟）
    - 分钟宽限 3.6 个自然日（周五夜盘周六凌晨收盘 → 周二上午）
    更旧说明该合约未被 pg-tickdata 同步覆盖（如新上市/非主力），
    读取方应落回 HTTP 源链。
    """
    if not bars:
        return True
    last = _parse_bar_clock(str(bars[-1].get("time") or ""))
    if last is None:
        return True
    now_naive = (now or datetime.now(_BJ_TZ)).replace(tzinfo=None)
    max_days = 5.0 if period == "1d" else 3.6
    return (now_naive - last).total_seconds() > max_days * 86400


async def _tickdata_first_when_closed(
    symbol: str, period: str, limit: int
) -> list[dict[str, Any]]:
    """闭市时段优先 pg-tickdata 永久历史（尾部够新才采用）。

    Redis 过期后不再依赖 sina/vvtr 即席拉取（sina 限流返回慢/空、
    vvtr 偶发残缺段，2026-08-24 pp2701 日线缺 8-11~8-21 即此路径）。
    永久库由每小时同步 + 收盘后权威修正持续沉淀；尾部过旧（未被
    同步覆盖的新合约）返回空，由调用方落回 HTTP 源链。
    """
    try:
        td = await fetch_tickdata_kline(symbol, period, end_time="", limit=limit)
    except Exception:
        logger.debug(
            "闭市 tickdata 优先读取失败 %s %s", symbol, period, exc_info=True
        )
        return []
    td = _filter_non_trading_bars(td, period)
    if td and not _closed_market_stale(td, period):
        return td
    return []


def _filter_non_trading_bars(
    bars: list[dict[str, Any]], period: str
) -> list[dict[str, Any]]:
    """剔除非交易日历的幽灵 bar（周日夜/周一凌晨/周六白天等）。

    vvtr 中继上游会把周末重放的报价聚合成 K 线随历史下发，
    2026-08-23（周日）实测整晚幽灵 1m/5m 经缓存污染图表。
    日线按周末日期剔除；分钟按 _is_minute_bar_in_session 的
    日历窗口剔除。无法解析时间的 bar 保守保留。
    """
    if period == "1d":
        out: list[dict[str, Any]] = []
        for b in bars:
            raw = str(b.get("time") or "")[:10]
            try:
                d = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                out.append(b)
                continue
            if d.weekday() < 5:  # 周一~周五
                out.append(b)
        return out
    return [
        b for b in bars if _is_minute_bar_in_session(str(b.get("time") or ""))
    ]


def _daily_bar_from_quote(quote: dict[str, Any], bar_time: str) -> dict[str, Any] | None:
    """用实时 quote 的交易所 OHLC 构造日线 bar（优先于新浪日线脏数据）"""
    try:
        last = float(quote.get("last_price") or 0)
        o = float(quote.get("open_price") or 0)
        h = float(quote.get("high_price") or 0)
        l = float(quote.get("low_price") or 0)
    except (TypeError, ValueError):
        return None
    if last <= 0:
        return None
    if o <= 0:
        o = last
    if h <= 0:
        h = max(o, last)
    if l <= 0:
        l = min(o, last)
    # 纠偏：high/low 至少包住 open/close
    h = max(h, o, last)
    l = min(l, o, last)
    avg = quote.get("avg_price")
    pos = quote.get("position")
    return _normalize_bar(
        {
            "time": bar_time,
            "open": o,
            "high": h,
            "low": l,
            "close": last,
            "volume": int(quote.get("volume") or 0),
            "settle": float(avg) if avg not in (None, 0, 0.0) else None,
            "open_interest": int(pos) if pos is not None else None,
        }
    )


async def _load_sina_quote(
    redis_conn: aioredis.Redis,
    symbol: str,
) -> dict[str, Any] | None:
    """日线 OHLC 只用新浪源键（CTP/SimNow 仿真 open 常与交易所不一致）。

    sina-bridge 容器写 source 键，本进程只读 Redis 不出网。
    """
    from app.services.market_keys import source_quote_key

    try:
        raw = await redis_conn.get(source_quote_key("sina", symbol.strip().lower()))
    except Exception:
        logger.exception("读取 sina 源键失败 %s", symbol)
        return None
    if not raw:
        return None
    try:
        q = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(q, dict) and float(q.get("last_price") or 0) > 0:
        return q
    return None


async def _load_live_daily_quote(
    redis_conn: aioredis.Redis,
    symbol: str,
) -> dict[str, Any] | None:
    """live 日线 forming 的 quote：real_ctp 渠道优先 CTP（交易所真实 OHLC），
    否则新浪源键兜底。"""
    try:
        from app.core.trading_mode import MODE_LIVE
        from app.services.market_channel import (
            CHANNEL_REAL_CTP,
            read_channel_from_redis,
        )

        channel = await read_channel_from_redis(redis_conn, MODE_LIVE)
        if channel == CHANNEL_REAL_CTP:
            raw = await redis_conn.get(
                f"market:live:source:real_ctp:{symbol.strip().lower()}"
            )
            if raw:
                q = json.loads(raw)
                if isinstance(q, dict) and float(q.get("last_price") or 0) > 0:
                    return q
    except Exception:
        logger.debug("读取 real_ctp quote 失败，回退新浪", exc_info=True)
    return await _load_sina_quote(redis_conn, symbol)


def _filter_bars_not_future(
    bars: list[dict[str, Any]],
    period: str,
    symbol: str = "",
) -> list[dict[str, Any]]:
    """丢掉明显未来的 bar（新浪偶发脏点 / 错误交易日标签）"""
    now = datetime.now(_BJ_TZ)
    if period == "1d":
        # 允许到当前期货交易日（周五夜盘 = 下周一）
        max_day = get_trading_day(now).strftime("%Y-%m-%d")
        out: list[dict[str, Any]] = []
        for b in bars:
            t = _bar_time_key("1d", str(b.get("time") or ""))
            if t and t <= max_day:
                out.append(b)
        return out
    # 分钟线时间键为周期起点；当前合法 forming 标签可能明显晚于 now。
    limit = now + timedelta(minutes=2)
    minutes = _PERIOD_MINUTES.get(period)
    if symbol and minutes is not None:
        official_end = period_end_for_product(
            extract_product_code(symbol), now, minutes
        )
        if official_end is not None:
            limit = official_end
    out = []
    for b in bars:
        dt = _parse_bar_clock(str(b.get("time") or ""))
        if dt is None:
            continue
        # naive 北京 wall clock
        if dt <= limit.replace(tzinfo=None):
            out.append(b)
    return out


def _daily_forming_first_session_start(symbol: str, trading_day: datetime) -> datetime:
    """交易日 bar 的首个交易段开始时刻（北京墙钟，naive）。

    有夜盘品种：夜盘在「上一交易日」晚间 21:00 开始（跳过周末——
    周一的夜盘在周五 21:00）。无夜盘品种：当日日盘 09:00。
    """
    from datetime import time as dtime

    code = extract_product_code(symbol)
    sessions = all_sessions(get_profile(code))
    has_night = any(s >= dtime(21, 0) for s, _ in sessions)

    day = trading_day.replace(hour=0, minute=0, second=0, microsecond=0)
    if has_night:
        prev = day - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev.replace(hour=21)
    return day.replace(hour=9)


async def _attach_realtime_bar(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    bars: list[dict[str, Any]],
    trading_mode: str = "live",
) -> list[dict[str, Any]]:
    """把 Redis 中当前 forming bar 合并进历史序列

    live 日线：新浪实时 OHLC；分钟：kline:realtime。
    virtual 日线/分钟：virtual quote + kline:virtual:realtime（7×24）。

    live 日线挂载条件（2026-08-24 修正）：交易日 T 的日 K 从其首个
    交易段开始就存在——周五 21:00 夜盘属下一交易日，周末休市期间
    该 bar 保留（内容=已收盘夜盘定格）。防幽灵两道闸：
    1. T 的首个交易段未开始不预埋（无夜盘品种周日晚不出现次日 bar）；
    2. 乱序重放帧拦截：qbar 与上一交易日收盘价偏离 >8% 不挂。
    """
    from app.core.trading_mode import MODE_VIRTUAL, normalize_trading_mode
    from app.services.kline_keys import realtime_kline_key

    mode = normalize_trading_mode(trading_mode)
    is_virtual = mode == MODE_VIRTUAL

    if period == "1d":
        if is_virtual:
            from app.services.market_keys import get_quote_json

            quote = await get_quote_json(redis_conn, MODE_VIRTUAL, symbol)
        else:
            quote = await _load_live_daily_quote(redis_conn, symbol)
        if quote and float(quote.get("last_price") or 0) > 0:
            # live：期货交易日；virtual 7×24：北京自然日
            if is_virtual:
                bar_time = datetime.now(_BJ_TZ).strftime("%Y-%m-%d")
            else:
                bar_time = get_trading_day(datetime.now(_BJ_TZ)).strftime(
                    "%Y-%m-%d"
                )
            if not is_virtual:
                # 闸 1：交易日首段未开始不预埋（内容无从产生）
                now_naive = datetime.now(_BJ_TZ).replace(tzinfo=None)
                t_day = datetime.strptime(bar_time, "%Y-%m-%d")
                first_start = _daily_forming_first_session_start(symbol, t_day)
                if now_naive < first_start:
                    return bars
            qbar = _daily_bar_from_quote(quote, bar_time)
            if qbar is not None:
                # 闸 2：乱序重放帧拦截（与上一交易日收盘价偏离 >8%）
                if bars:
                    try:
                        prev_close = float(bars[-1].get("close") or 0)
                    except (TypeError, ValueError):
                        prev_close = 0.0
                    if prev_close > 0:
                        ref = max(
                            abs(float(qbar.get("open") or 0) - prev_close),
                            abs(float(qbar.get("close") or 0) - prev_close),
                        ) / prev_close
                        if ref > 0.08:
                            logger.warning(
                                "日线 forming 价格异常（偏离 %.1f%%，%s %s），不挂",
                                ref * 100, symbol, bar_time,
                            )
                            return bars
                if is_virtual:
                    qbar["source"] = "virtual"
                if not bars:
                    return [qbar]
                last_key = _bar_time_key("1d", str(bars[-1].get("time") or ""))
                if last_key == bar_time:
                    bars[-1] = qbar
                    return bars
                if last_key < bar_time:
                    bars.append(qbar)
                    return bars
                for i in range(len(bars) - 1, -1, -1):
                    if _bar_time_key("1d", str(bars[i].get("time") or "")) == bar_time:
                        bars[i] = qbar
                        return bars
                bars.append(qbar)
                return bars
        return bars

    try:
        raw = await redis_conn.get(realtime_kline_key(mode, symbol, period))
    except Exception:
        logger.exception("读取 realtime kline 失败 %s %s", symbol, period)
        return bars
    if not raw:
        return bars
    try:
        payload = json.loads(raw)
        rt = payload.get("bar") if isinstance(payload, dict) else None
    except Exception:
        return bars
    if not isinstance(rt, dict) or not rt.get("time"):
        return bars
    rt = _normalize_bar(dict(rt))
    # live 分钟：必须落在国内交易时段；virtual 7×24 放行
    if not is_virtual:
        if not _is_minute_bar_in_session(str(rt.get("time") or "")):
            return bars
        if int(rt.get("volume") or 0) <= 0 and not is_trading_hours():
            return bars
    # 拒绝超过“当前官方周期结束时刻”的未来 forming。
    rt_dt = _parse_bar_clock(str(rt.get("time") or ""))
    if rt_dt is not None:
        now = datetime.now(_BJ_TZ)
        limit = now + timedelta(minutes=2)
        if not is_virtual:
            minutes = _PERIOD_MINUTES.get(period)
            if minutes is not None:
                official_end = period_end_for_product(
                    extract_product_code(symbol), now, minutes
                )
                if official_end is not None:
                    limit = official_end
        if rt_dt > limit.replace(tzinfo=None):
            return bars

    if not bars:
        return [rt]
    last = bars[-1]
    last_key = _bar_time_key(period, str(last.get("time") or ""))
    rt_key = _bar_time_key(period, str(rt.get("time") or ""))
    if not rt_key:
        return bars
    if rt_key == last_key:
        # 同根合并——两侧各缺一段，必须字段级取优：
        # - open 保留历史（窗口起点开盘价，避免中途接入改开盘）
        # - H/L 取两侧极值并集：forming 在后端重启/状态丢失后会从重建时刻
        #   起步（丢窗口头部极值），历史聚合缓存则缺最新一段；
        #   任取一侧都会丢极值，甚至拼出 open>high 的自相矛盾 bar
        # - close/settle/open_interest 用 realtime（最新）
        # - volume 取较大者（口径不同：历史为已聚总量快照，forming 为
        #   重启后增量；取大更接近真实）
        merged = dict(last)
        for key in ("close", "settle", "open_interest"):
            if key in rt and rt[key] is not None:
                merged[key] = rt[key]
        try:
            hist_high = float(merged.get("high") or 0)
            rt_high = float(rt.get("high") or 0)
            merged["high"] = max(hist_high, rt_high)
            hist_low = float(merged.get("low") or 0)
            rt_low = float(rt.get("low") or 0)
            if hist_low > 0 and rt_low > 0:
                merged["low"] = min(hist_low, rt_low)
            else:
                merged["low"] = hist_low or rt_low
        except (TypeError, ValueError):
            for key in ("high", "low"):
                if rt.get(key) is not None:
                    merged[key] = rt[key]
        if rt.get("volume") is not None:
            try:
                merged["volume"] = max(
                    int(merged.get("volume") or 0), int(rt.get("volume") or 0)
                )
            except (TypeError, ValueError):
                merged["volume"] = rt.get("volume")
        # 兜底纠偏：high/low 至少包住 open/close（防御任何来源的脏数据）
        o = float(merged.get("open") or 0)
        c = float(merged.get("close") or 0)
        if o > 0 and c > 0:
            merged["high"] = max(float(merged.get("high") or 0), o, c)
            merged["low"] = min(float(merged.get("low") or 1e18), o, c)
        bars[-1] = _normalize_bar(merged)
        return bars
    if rt_key > last_key:
        bars.append(rt)
        return bars
    return bars


async def _merge_daily_history(
    cached_bars: list[dict[str, Any]],
    symbol: str,
    end_time: str,
    limit: int,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """日线懒加载：合并 PG 历史 + Sina 缓存数据"""
    end_date = end_time[:10]  # 取日期部分 "YYYY-MM-DD"
    oldest_cached = cached_bars[0]["time"][:10] if cached_bars else ""

    # 过滤缓存中 end_time 之前的 bar
    filtered = [b for b in cached_bars if b["time"][:10] < end_date]

    # 请求范围完全在 PG 历史中
    if end_date < oldest_cached:
        code = get_code_for_symbol(symbol)
        if not code:
            return filtered[-limit:] if limit > 0 else filtered
        pg_bars = await query_history_bars(session, code, end_date, limit)
        return pg_bars

    # 请求范围在缓存内，过滤后数量足够
    if limit > 0 and len(filtered) >= limit:
        return filtered[-limit:]

    # 过滤后不够：从 PG 补充更早的数据
    code = get_code_for_symbol(symbol)
    if not code:
        return filtered[-limit:] if limit > 0 else filtered

    pg_end = filtered[0]["time"][:10] if filtered else end_date
    pg_limit = (limit - len(filtered)) if limit > 0 else 200
    pg_bars = await query_history_bars(session, code, pg_end, pg_limit)
    return pg_bars + filtered


async def _merge_minute_history(
    cached_bars: list[dict[str, Any]],
    symbol: str,
    period: str,
    end_time: str,
    limit: int,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """分钟线懒加载：合并 PG 历史分钟线 + Sina 缓存数据。

    PG 存的是 1m（tqsdk 导入），5/15/30/60m 用 aggregate_from_1m 现算。
    新浪缓存只有约 1023 根（近期），当 end_time 超出缓存范围时从 PG 补更早历史。
    """
    code = get_code_for_symbol(symbol)
    if not code:
        # 无法识别品种，退回标准切片
        bars = [b for b in cached_bars if b.get("time", "") < end_time]
        return bars[-limit:] if limit > 0 else bars

    # 过滤缓存中 end_time 之前的 bar
    filtered = [b for b in cached_bars if b.get("time", "") < end_time]
    oldest_cached = filtered[0]["time"] if filtered else (cached_bars[0]["time"] if cached_bars else "")

    # 从 PG 拉 1m 历史（最多 8964 根，约 6 个月）
    pg_1m = await query_minute_history(session, code, "1m", end_time, 8964)
    if not pg_1m:
        # PG 无分钟历史，退回新浪切片
        return filtered[-limit:] if limit > 0 else filtered

    # 多周期聚合（5/15/30/60m），1m 直接用
    if period == "1m":
        pg_bars = pg_1m
    else:
        pg_bars = aggregate_from_1m(pg_1m, period, symbol)

    if not pg_bars:
        return filtered[-limit:] if limit > 0 else filtered

    # 合并 PG 历史 + 新浪近期（PG 在前更早，新浪在后更新），按时间排序去重
    merged = pg_bars + filtered
    seen: dict[str, dict[str, Any]] = {}
    for b in merged:
        t = str(b.get("time", ""))
        # 新浪时间精度到秒，PG 也是秒；同时间键新浪覆盖 PG（新浪含实时性）
        seen[t] = b
    merged = sorted(seen.values(), key=lambda b: str(b.get("time", "")))
    if limit > 0:
        merged = merged[-limit:]
    return merged


async def _fetch_from_provider(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    session: AsyncSession | None = None,
    is_stale: Callable[[list[dict[str, Any]]], bool] | None = None,
) -> list[dict[str, Any]]:
    """从多数据源拉取 K 线（主渠道 + 备用渠道降级 + 健康冷却）

    主/备渠道来自管理后台配置（Redis 镜像 → DB → 默认主=东财,备=新浪）。
    传入 is_stale 时，某源返回的数据过旧会跳过并继续降级到下一源。
    """
    from app.core.config import load_settings
    from app.data.kline_provider import fetch_kline_with_fallback
    from app.services.kline_source_cfg import get_kline_source_config

    primary, backups = await get_kline_source_config(redis_conn, session)
    settings = load_settings()
    return await fetch_kline_with_fallback(
        redis_conn,
        symbol,
        period,
        primary=primary,
        backups=backups,
        remote_base=settings.kline_remote_base,
        is_stale=is_stale,
    )


async def _fetch_from_pg(
    session: AsyncSession,
    symbol: str,
    period: str,
) -> list[dict[str, Any]]:
    """PG 历史兜底：日线 kline_history / 分钟 kline_minute（离线导入数据）

    所有实时源（新浪/tqsdk/东财/远端）都失败时使用，保证 K 线图仍有历史可画。
    分钟表只存 1m，多周期用 aggregate_from_1m 现算。
    """
    code = get_code_for_symbol(symbol)
    if not code:
        return []
    try:
        if period == "1d":
            from app.services.kline_history import query_history_bars

            today = datetime.now(_BJ_TZ).strftime("%Y-%m-%d")
            return await query_history_bars(session, code, today, 500)
        from app.services.kline_minute import query_minute_history

        # kline_minute 表 code 存小写（导入脚本 code.lower()）
        pg_1m = await query_minute_history(session, code.lower(), "1m", None, 8964)
        if not pg_1m:
            return []
        if period == "1m":
            return pg_1m
        return aggregate_from_1m(pg_1m, period, symbol)
    except Exception:
        logger.exception("PG K 线兜底查询失败 %s %s", symbol, period)
        return []


# ========== 实时更新 ==========


# 分钟周期长度（与新浪 1m 聚合、历史 time 键一致）
_PERIOD_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
}


def _exchange_clock(quote: dict[str, Any], fallback: datetime) -> datetime:
    """从 quote 提取交易所行情时间（北京时间 aware），失败回退本地钟。

    forming 分桶必须用交易所时间对齐秒/分钟边界（与 VVTR 历史 K 线的
    交易所桶一致），本地钟只做兜底——调度延迟、事件循环阻塞、服务器
    时钟偏移都不应把 tick 归错桶。

    支持的 tick_time 形态：
    - 17 位数字（VVTR v2：YYYYMMDDHHmmssSSS）
    - "YYYY-MM-DD HH:MM:SS[.fff]"（VVTR v1 / iso）
    - 纯时刻（新浪 "HH:MM:SS"）：无日期不可靠（夜盘跨午夜），直接回退。
    与本地钟偏差超过 _EXCHANGE_CLOCK_MAX_DRIFT 视为脏数据，同样回退。

    tick_time_iso（quote_schema 归一字段）优先——入口已统一归一的 quote
    走单一路径;无 iso（纯时刻/老缓存）回退下面的兼容分支。
    """
    iso = str(quote.get("tick_time_iso") or "").strip()
    if iso:
        try:
            aware = (
                datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_BJ_TZ)
            )
            if abs(aware - fallback) <= _EXCHANGE_CLOCK_MAX_DRIFT:
                return aware
            return fallback
        except ValueError:
            pass
    raw = str(quote.get("tick_time") or "").strip()
    dt: datetime | None = None
    if raw:
        try:
            if len(raw) == 17 and raw.isdigit():
                dt = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
            elif len(raw) >= 19 and raw[4] == "-" and raw[10] in " T":
                dt = datetime.strptime(raw[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = None
    if dt is None:
        return fallback
    # 解析出的都是北京墙钟 → 统一挂 _BJ_TZ（与调用方 now 同型，aware）
    aware = dt.replace(tzinfo=_BJ_TZ)
    if abs(aware - fallback) > _EXCHANGE_CLOCK_MAX_DRIFT:
        return fallback
    return aware


def _get_period_start_time(
    period: str,
    now: datetime,
    symbol: str = "",
) -> datetime:
    """计算当前 K 线 bar 的时间键（bar 起点时刻）

    分钟线与 VVTR 历史 K 线（b=bob 起点）语义一致：10:37 的 15m bar →
    10:30；09:00:30 的 1m bar → 09:00。日线仍按期货交易日（夜盘 21:00
    起归属下一交易日）。

    历史注：曾按新浪口径用【周期结束时刻】标记；切换 VVTR 主源后
    历史（起点）与 forming（结束）错位一个周期——forming 带未来
    时间戳、跨周期时同一根 K 线出现两个时间戳（2026-08-21 实测
    K 线图错乱），统一为起点语义（交易轴对齐桶起点，跨休盘长周期
    由 period_start_for_product 按交易分钟轴回退，非墙钟近似）。
    """
    if period == "1d":
        return get_trading_day(now)
    minutes = _PERIOD_MINUTES.get(period)
    if minutes is None:
        return now.replace(second=0, microsecond=0)
    if symbol:
        aligned_start = period_start_for_product(
            extract_product_code(symbol),
            now,
            minutes,
        )
        if aligned_start is not None:
            return aligned_start
    total = now.hour * 60 + now.minute
    start_total = (total // minutes) * minutes
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=start_total)


# 历史 K 线缓存保留的最多 bar 数（封顶，防止无限增长）
_MAX_HISTORY_BARS = 500


async def append_closed_bar(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    bar: dict[str, Any],
) -> None:
    """跨周期时把刚收盘的 forming bar 固化进历史 K 线缓存。

    背景：live 分钟 K 线跨周期时，旧 forming bar 只存在于内存 state 与
    kline:realtime 单 key 中；若新浪历史接口恰好没返回该 bar（偶发永久丢点），
    这根已收盘 bar 就会丢失，导致 AI 任务的 extract_closed_bar 取到更旧的 bar
    而判定 no_new_bar、静默停摆一个周期。

    逻辑：仅当历史缓存已存在时追加/覆盖该 bar（缓存不存在则跳过——
    让 get_kline_history 下次从新浪重拉即可，不新建空 list 覆盖新浪真实历史）。
    """
    if not bar or not bar.get("time"):
        return
    cache_key = _history_cache_key(symbol, period)
    try:
        raw = await redis_conn.get(cache_key)
    except Exception:
        logger.exception("读取历史 K 线缓存失败 %s %s", symbol, period)
        return
    if raw is None:
        # 缓存不存在（首次访问/已过期）：交由 get_kline_history 从新浪重拉，不强行固化
        return
    try:
        bars = json.loads(raw)
        if not isinstance(bars, list):
            return
    except Exception:
        logger.exception("解析历史 K 线缓存失败 %s %s", symbol, period)
        return

    closed = _normalize_bar(dict(bar))
    tkey = _bar_time_key(period, str(closed.get("time") or ""))
    if not tkey:
        return
    # 同键覆盖（幂等，避免重复）；否则追加
    replaced = False
    for i, b in enumerate(bars):
        if _bar_time_key(period, str(b.get("time") or "")) == tkey:
            bars[i] = closed
            replaced = True
            break
    if not replaced:
        bars.append(closed)
    # 封顶：只保留最近 N 根，按时间升序
    bars = bars[-_MAX_HISTORY_BARS:]
    ttl = _get_ttl(period)
    try:
        await redis_conn.set(
            cache_key,
            json.dumps(bars, ensure_ascii=False),
            ex=ttl,
        )
    except Exception:
        logger.exception("回写历史 K 线缓存失败 %s %s", symbol, period)


def _realtime_state_payload(
    symbol: str,
    period: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """构造可在进程重启后恢复的实时桶状态。"""
    return {
        "symbol": symbol,
        "period": period,
        "bar": state["bar"],
        "bar_time": state["bar_time"],
        "period_start_volume": state["period_start_volume"],
        "open_locked": bool(state.get("open_locked")),
        "version": int(state.get("version") or 0),
    }


async def _solidify_closed_state(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    state: dict[str, Any] | None,
    persist_masters: set[str] | None,
) -> None:
    """把已收盘的 forming bar 固化进历史 K 线缓存（1m 主力另写 PG）。

    跨周期收线与休市清理两路共用。恢复态（recovered）跳过：重启丢过
    本周期前段快照，固化会覆盖权威历史源的正确收盘值。
    """
    if state is None or state.get("recovered"):
        return
    closed_bar = dict(state.get("bar") or {})
    if not closed_bar.get("time"):
        return
    # 周末/非交易时段的幽灵 forming（数据源重放帧遗留）不固化，
    # 与读路径 _filter_non_trading_bars 同一过滤口径。
    if not _is_minute_bar_in_session(str(closed_bar["time"])):
        return
    try:
        await append_closed_bar(redis_conn, symbol, period, closed_bar)
    except Exception:
        logger.exception("固化已收盘 K 失败 %s %s", symbol, period)
    # 1m bar 实时增量写入 PG（SimNow 自产历史）
    # VVTR 定时修正会覆盖为权威值；只写当前主力——
    # 品种级序列不容非主力合约混入（移仓期交错=分时坏 K）
    if period == "1m" and (persist_masters is None or symbol in persist_masters):
        try:
            from app.services.kline_persist import persist_closed_bar

            await persist_closed_bar(symbol, closed_bar)
        except Exception:
            logger.debug("1m bar 写 PG 失败 %s", symbol, exc_info=True)


async def _load_realtime_state(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
) -> dict[str, Any] | None:
    """从 Redis 恢复完整 forming 状态；旧版仅含 bar 的载荷不用于续算。"""
    try:
        raw = await redis_conn.get(_realtime_cache_key(symbol, period))
        if not raw:
            return None
        payload = json.loads(raw)
    except Exception:
        return None
    bar = payload.get("bar") if isinstance(payload, dict) else None
    bar_time = payload.get("bar_time") if isinstance(payload, dict) else None
    baseline = (
        payload.get("period_start_volume")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(bar, dict) or not bar_time or baseline is None:
        return None
    try:
        baseline_value = int(baseline)
    except (TypeError, ValueError):
        return None
    return {
        "bar": _normalize_bar(dict(bar)),
        "bar_time": str(bar_time),
        "period_start_volume": baseline_value,
        "open_locked": bool(payload.get("open_locked")),
        "version": int(payload.get("version") or 0),
        # 恢复态标记：进程重启丢掉了本周期前段的快照，bar 的 OHLC/Vol
        # 是残缺的（2026-08-21 发布重启后 21:15 bar 固化了残缺值覆盖
        # vvtr 权威收盘即此路径）。收线时禁止固化，交给权威历史源。
        "recovered": True,
    }


async def _cache_realtime_state(
    redis_conn: aioredis.Redis,
    symbol: str,
    period: str,
    state: dict[str, Any],
) -> None:
    await redis_conn.set(
        _realtime_cache_key(symbol, period),
        json.dumps(
            _realtime_state_payload(symbol, period, state),
            ensure_ascii=False,
        ),
        ex=_TTL_REALTIME,
    )


async def heal_recovered_forming(redis_conn: aioredis.Redis) -> int:
    """用 vvtr 当根权威 OHLC 治愈恢复态 forming（升级重启后调用一次）

    重启恢复的 forming 桶丢过前段快照（H/L 窄、基线乱），收线虽已禁止
    固化，但盘中显示仍是残缺值。/kline/current 提供当根（含未收线）
    权威 OHLCV（V 为当根增量口径）：时间键匹配的桶直接替换为权威值，
    增量基线用 当日累计量-当根V 重建，替换后清除 recovered（可正常固化）。
    拿不到 quote 基线或时间不匹配则保守跳过。
    """
    # 收集恢复态桶：{period: [symbol...]}
    recovered: dict[str, list[str]] = {}
    for symbol, periods in _realtime_state.items():
        for period, state in periods.items():
            if isinstance(state, dict) and state.get("recovered"):
                recovered.setdefault(period, []).append(symbol)
    if not recovered:
        return 0

    healed = 0
    for period, symbols in recovered.items():
        try:
            def _fetch() -> dict[str, dict[str, Any]]:
                from app.connectors.vvtr.config import get_vvtr_config_sync
                from app.connectors.vvtr.http_client import fetch_vvtr_current_bars

                cfg = get_vvtr_config_sync()
                if not cfg.api_key or not cfg.kline_enabled:
                    return {}
                return fetch_vvtr_current_bars(
                    cfg.api_key, symbols, period, base_url=cfg.base_url
                )

            current = await asyncio.to_thread(_fetch)
        except Exception:
            logger.debug("拉取当根权威 bar 失败 %s", period, exc_info=True)
            continue
        if not current:
            continue

        for symbol in symbols:
            state = _realtime_state.get(symbol, {}).get(period)
            if not isinstance(state, dict) or not state.get("recovered"):
                continue  # 已被跨周期滚掉
            auth = current.get(symbol)
            if not auth or auth.get("time") != state.get("bar_time"):
                continue  # 时间键不匹配（上游滞后/已跨周期）→ 保守跳过
            try:
                qraw = await redis_conn.get(f"market:live:quote:{symbol}")
                total_vol = int(json.loads(qraw).get("volume") or 0) if qraw else 0
                bar_vol = int(auth.get("volume") or 0)
                if total_vol <= 0 or bar_vol > total_vol:
                    continue  # 基线不可重建 → 跳过，保留禁固化保护
                _apply_authoritative_bar(state, auth, total_vol)
                await _cache_realtime_state(redis_conn, symbol, period, state)
                healed += 1
                logger.info(
                    "恢复态 forming 已治愈 %s %s（权威 OHLC 替换）",
                    symbol, period,
                )
            except Exception:
                logger.debug("治愈 forming 失败 %s %s", symbol, period, exc_info=True)
    return healed


def _apply_authoritative_bar(
    state: dict[str, Any],
    auth: dict[str, Any],
    total_volume: int,
) -> None:
    """用权威 bar 原地替换 forming 桶内容（版本递增 + 修正标记）。

    version 递增保证 WS 下游（前端按 version 幂等覆盖）能识别这是同一根
    bar 的修正而非旧数据回放；kind=correction 便于前端/日志区分。
    """
    bar = _normalize_bar(dict(auth))
    state["version"] = int(state.get("version") or 0) + 1
    bar["version"] = state["version"]
    bar["kind"] = "correction"
    state["bar"] = bar
    state["period_start_volume"] = max(0, total_volume - int(auth.get("volume") or 0))
    state.pop("recovered", None)  # 已权威化，恢复正常固化资格


def _bars_materially_differ(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """两根同时间键 bar 的 OHLCV 是否有实质差异（浮点容差内视为一致）"""
    for key in ("open", "high", "low", "close"):
        try:
            if abs(float(a.get(key) or 0) - float(b.get(key) or 0)) > 1e-9:
                return True
        except (TypeError, ValueError):
            return True
    try:
        if int(a.get("volume") or 0) != int(b.get("volume") or 0):
            return True
    except (TypeError, ValueError):
        return True
    return False


async def reconcile_forming_with_vvtr(
    redis_conn: aioredis.Redis,
    symbols: list[str] | None = None,
    periods: tuple[str, ...] = ("1m", "5m", "15m", "30m", "60m"),
) -> int:
    """盘中对账：用 VVTR /kline/current 当根权威 OHLCV 校正 forming 桶。

    背景：forming 由本地快照聚合（丢帧/渠道切换/时钟抖动都可能让当根
    bar 偏离权威值），而权威历史要到收盘后 01:00/15:05 的 kline_sync 才
    修正。盘中每 N 分钟抽样对账，时间键匹配且实质差异时原地替换：
    version 递增 + kind=correction，WS 下游按 version 幂等覆盖收敛。

    保守策略：上游滞后（权威 bar 时间键 ≠ 当前桶）/拉取失败/基线不可
    重建 → 一律跳过，绝不用旧权威值覆盖新桶。VVTR HTTP 全局 1 rps，
    每周期一次批量请求（多合约一次带走），调用方需控制节奏。
    """
    targets = {
        sym: periods_map
        for sym, periods_map in _realtime_state.items()
        if not symbols or str(sym).lower() in {str(s).lower() for s in symbols}
    }
    if not targets:
        return 0

    corrected = 0
    for period in periods:
        sym_list = [
            sym
            for sym, periods_map in targets.items()
            if isinstance(periods_map.get(period), dict)
            and periods_map[period].get("bar_time")
        ]
        if not sym_list:
            continue
        try:
            def _fetch() -> dict[str, dict[str, Any]]:
                from app.connectors.vvtr.config import get_vvtr_config_sync
                from app.connectors.vvtr.http_client import fetch_vvtr_current_bars

                cfg = get_vvtr_config_sync()
                if not cfg.api_key or not cfg.kline_enabled:
                    return {}
                return fetch_vvtr_current_bars(
                    cfg.api_key, sym_list, period, base_url=cfg.base_url
                )

            current = await asyncio.to_thread(_fetch)
        except Exception:
            logger.debug("盘中对账拉取当根权威 bar 失败 %s", period, exc_info=True)
            continue
        if not current:
            continue

        for symbol in sym_list:
            state = _realtime_state.get(symbol, {}).get(period)
            if not isinstance(state, dict) or not state.get("bar_time"):
                continue
            auth = current.get(symbol)
            if not auth or auth.get("time") != state.get("bar_time"):
                continue  # 上游滞后/已跨周期 → 保守跳过
            bar = state.get("bar") or {}
            if not _bars_materially_differ(bar, auth):
                continue
            try:
                qraw = await redis_conn.get(f"market:live:quote:{symbol}")
                total_vol = int(json.loads(qraw).get("volume") or 0) if qraw else 0
                if total_vol <= 0 or int(auth.get("volume") or 0) > total_vol:
                    continue  # 增量基线不可重建 → 跳过本次，等下一轮
                _apply_authoritative_bar(state, auth, total_vol)
                await _cache_realtime_state(redis_conn, symbol, period, state)
                corrected += 1
                logger.info(
                    "盘中对账修正 forming %s %s（version=%s）",
                    symbol, period, state["version"],
                )
            except Exception:
                logger.debug("盘中对账修正失败 %s %s", symbol, period, exc_info=True)
    return corrected


async def update_realtime_klines(
    redis_conn: aioredis.Redis,
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """基于行情数据更新各周期的最新 K 线 bar

    返回所有更新的 bar 列表，用于 WebSocket 推送。

    规则：
    - 日线：全天可更新（含收盘后定格），open=交易日开盘价
    - 分钟：仅交易时段更新；休市不写 18:xx 幽灵 K
    """
    now = datetime.now(_BJ_TZ)
    market_open = is_trading_hours()
    updated_bars: list[dict[str, Any]] = []

    # 1m PG 固化只写当前主力：kline_minute 是品种级序列（code 键），
    # 非主力合约（移仓期新旧并存/静态合约表滞后切换）一旦写入会与主力
    # bar 交错，分时图出现 ±月间价差的坏 K（2026-08-24 fb 实测 ±7% 横跳）。
    try:
        from app.services.contract_master import get_master_contracts

        _master_entries = await get_master_contracts(redis_conn)
        persist_masters = {
            str(e.get("master") or "").strip().lower()
            for e in (_master_entries or {}).values()
            if isinstance(e, dict)
        }
    except Exception:
        logger.debug("主力集合读取失败，1m 固化放行全部合约", exc_info=True)
        persist_masters = None  # None = 不限制（读取失败时不丢数据）

    for quote in quotes:
        symbol: str = quote["symbol"]
        last_price: float = quote["last_price"]
        total_volume: int = quote.get("volume", 0)
        # 分桶时钟：优先交易所 tick_time（与 VVTR 历史桶同轴），本地钟兜底
        clock = _exchange_clock(quote, now)
        # 分钟线分桶时钟：整分 00 秒的 tick 是上一分钟的收盘打印（与深历史
        # pg-tickdata 的 eob 折叠口径同轴），折回上一分钟内侧再分桶；
        # 日线用原始时钟——21:00 开盘整点必须归入新交易日。
        minute_clock = (
            fold_tick_minute_edge(extract_product_code(symbol), clock)
            if symbol
            else clock
        )
        # 品种级休市（比粗粒度更准）；粗粒度关则分钟全停。
        # 收盘整点打印折回内侧后仍属交易中（更新收盘桶），迟到帧才走固化清理
        symbol_open = market_open and is_symbol_trading(symbol, minute_clock)

        if symbol not in _realtime_state:
            _realtime_state[symbol] = {}

        for period in KLINE_PERIODS:
            is_daily = period == "1d"

            period_start = _get_period_start_time(
                period, clock if is_daily else minute_clock, symbol
            )
            # 日线与历史一致用 YYYY-MM-DD，避免与历史 bar 重复
            if is_daily:
                bar_time = period_start.strftime("%Y-%m-%d")
            else:
                bar_time = period_start.strftime("%Y-%m-%d %H:%M:%S")
                # 双保险：分钟 bar 时间必须在交易时段内
                if not _is_minute_bar_in_session(bar_time):
                    continue

            state = _realtime_state[symbol].get(period)
            if state is None and not is_daily:
                state = await _load_realtime_state(redis_conn, symbol, period)
            # 兼容旧内存态：历史 bar_time 可能带 00:00:00
            state_time = str(state.get("bar_time") or "") if state else ""
            state_key = _bar_time_key(period, state_time)
            new_key = _bar_time_key(period, bar_time)
            position = quote.get("position")

            # 休盘时只保留尚未封桶、确实跨休盘延续的长周期状态。
            # 「未封桶」按交易分钟轴的当前桶收线时刻判断（跨午休的
            # 30m/60m 桶墙钟跨度含休盘，墙钟加法会误判已收线）。
            if not is_daily and not symbol_open:
                bucket_end = (
                    period_end_for_product(
                        extract_product_code(symbol),
                        minute_clock,
                        _PERIOD_MINUTES.get(period, 0),
                    )
                    if symbol
                    else None
                )
                if (
                    state is not None
                    and state_key == new_key
                    and (bucket_end is None or bucket_end > minute_clock)
                ):
                    _realtime_state[symbol][period] = state
                    try:
                        await _cache_realtime_state(
                            redis_conn, symbol, period, state
                        )
                    except Exception:
                        pass
                    continue
                # 清理前先固化：已收线的末根 bar 落进历史缓存再清态。
                # 否则休市期间（周末重放 tick/隔夜迟到帧）状态被直接
                # 丢弃，真实末根只能依赖外部源回补——源滞后时刷新即
                # 丢根（2026-08-25 夜盘末根刷新消失路径之一）。
                await _solidify_closed_state(
                    redis_conn, symbol, period, state, persist_masters
                )
                _realtime_state[symbol].pop(period, None)
                try:
                    await redis_conn.delete(_realtime_cache_key(symbol, period))
                except Exception:
                    pass
                continue

            # 休市期间（周末/盘间/深夜）不用 quote 更新日线 forming：
            # 数据源在非交易时段推送的报价会把脏 OHLC 预埋进下一交易日
            # 的日线 bar（2026-08-23 周日 vvtr 幽灵报价实测污染 8-24 日线）。
            # 收盘后的权威日线由 kline_sync 用 VVTR 历史修正，不依赖本路径。
            if is_daily and not market_open:
                continue

            if state is not None:
                _realtime_state[symbol][period] = state

            # 检测是否跨入新周期
            if state is None or state_key != new_key:
                # 分钟级跨周期：先把上一根（已收盘）bar 固化进历史缓存，
                # 避免新浪历史接口偶发丢点导致该 bar 永久缺失、AI 任务静默停摆。
                # 日线全天更新不跨周期，无需固化。
                # 恢复态（recovered）跳过固化：重启丢过本周期前段快照，
                # bar 残缺，固化会覆盖权威历史源的正确收盘值。
                if state is not None and not is_daily:
                    await _solidify_closed_state(
                        redis_conn, symbol, period, state, persist_masters
                    )
                if is_daily:
                    # 日线 = 一个期货交易日：昨夜 21:00 → 今日 15:00
                    # open 必须用交易所「开盘价」(quote.open_price)，即交易日首开
                    # 绝不能用昨结 pre_settle
                    h_daily = float(quote.get("high_price") or last_price)
                    l_daily = float(quote.get("low_price") or last_price)
                    o_raw = quote.get("open_price")
                    o_daily = float(o_raw) if o_raw not in (None, 0, 0.0) else last_price
                    avg = quote.get("avg_price")
                    bar = {
                        "time": bar_time,
                        "open": o_daily,
                        "high": h_daily,
                        "low": l_daily,
                        "close": last_price,
                        "volume": total_volume,
                        "settle": float(avg) if avg not in (None, 0, 0.0) else None,
                        "open_interest": int(position) if position is not None else None,
                    }
                else:
                    # 分钟级：本周期首 tick 作为 open，之后锁定
                    bar = {
                        "time": bar_time,
                        "open": last_price,
                        "high": last_price,
                        "low": last_price,
                        "close": last_price,
                        "volume": 0,
                        "settle": None,
                        "open_interest": int(position) if position is not None else None,
                    }
                state = {
                    "bar": bar,
                    "bar_time": bar_time,
                    "period_start_volume": total_volume,
                    # 开盘价锁定：日线/分钟均不再改 open
                    "open_locked": True,
                    # 版本号：每次更新 +1，WS 推送带 version 供前端幂等覆盖
                    "version": 1,
                }
                bar["version"] = 1
                _realtime_state[symbol][period] = state
            else:
                # 更新现有 bar（统一时间格式）
                bar: dict[str, Any] = state["bar"]
                bar["time"] = bar_time
                state["bar_time"] = bar_time
                state["version"] = int(state.get("version") or 0) + 1
                bar["version"] = state["version"]
                bar.pop("kind", None)  # 修正标记只在权威替换时出现，常规更新清除
                bar["close"] = last_price
                if position is not None:
                    bar["open_interest"] = int(position)

                if is_daily:
                    # 日线：高低/总量跟交易所；开盘价锁定交易日首开
                    bar["high"] = quote.get("high_price", bar["high"])
                    bar["low"] = quote.get("low_price", bar["low"])
                    bar["volume"] = total_volume
                    avg = quote.get("avg_price")
                    if avg not in (None, 0, 0.0):
                        bar["settle"] = float(avg)
                    if not state.get("open_locked"):
                        o_raw = quote.get("open_price")
                        if o_raw not in (None, 0, 0.0):
                            bar["open"] = float(o_raw)
                            state["open_locked"] = True
                    else:
                        # 已锁定：若 open 仍等于昨结而 open_price 不同，纠正旧 bug
                        pre_settle = quote.get("pre_close")
                        o_raw = quote.get("open_price")
                        if (
                            o_raw not in (None, 0, 0.0)
                            and pre_settle is not None
                            and abs(float(bar.get("open") or 0) - float(pre_settle)) < 1e-9
                            and abs(float(o_raw) - float(pre_settle)) > 1e-6
                        ):
                            bar["open"] = float(o_raw)
                else:
                    # 分钟级：open 锁定；H/L 跟 tick；量=周期内增量
                    bar["high"] = max(bar["high"], last_price)
                    bar["low"] = min(bar["low"], last_price)

                    prev_vol = state["period_start_volume"]
                    volume_delta = total_volume - prev_vol
                    if volume_delta < 0:
                        state["period_start_volume"] = total_volume
                        bar["volume"] = 0
                    else:
                        bar["volume"] = volume_delta

            # 缓存到 Redis
            await _cache_realtime_state(redis_conn, symbol, period, state)

            updated_bars.append({
                "symbol": symbol,
                "period": period,
                "bar": state["bar"],
            })

    return updated_bars
