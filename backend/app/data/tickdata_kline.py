"""TickData 历史 K线数据源 —— 从 pg-tickdata 读取全周期历史 K线

pg-tickdata（postgres:18，库 tickdata）存储 2005 年至今的期货全周期 K线：
- 表 kline：period(smallint 1/5/15/30/60/1440) × exchange × instrument × ts
           × open/high/low/close × volume(bigint) × amount × position(持仓量)
- 按 ts 月分区（264 个），主键 (period, instrument, ts) 完备
- 查询命中主键索引 + 分区裁剪（只扫目标月分区），单次 <50ms

定位：K线图历史回溯的最高优先级源（2005 起无限回溯）。
- 未配置 TICKDATA_POSTGRES_HOST 时自动跳过（返回空），降级到 HTTP/PG，不影响现网。
- 任何异常都捕获并返回空，绝不抛到上层（保证现网行情零影响）。

与主库（qihuo）隔离：独立 asyncpg 连接池，不依赖主进程 session_factory。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.config import load_settings

logger = logging.getLogger("qihuo.tickdata")

# 现系统 period 字符串 → pg-tickdata kline.period(smallint)
# 1440 = 1 天的分钟数
PERIOD_MAP: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1d": 1440,
}

# 北京时间（pg-tickdata ts 为 naive 北京时间）
_BJ_TZ = timezone(timedelta(hours=8))

# 每周期回溯窗口（天）：查询强制带 ts 下限，触发月分区裁剪，
# 避免 `instrument=? ORDER BY ts DESC LIMIT n` 跨 264 个月分区全扫（会超时）。
# 窗口按"每交易日 240 根 × 覆盖 limit(默认500) + 节假日余量"估算。
_LOOKBACK_DAYS: dict[str, int] = {
    "1m": 7,
    "5m": 20,
    "15m": 50,
    "30m": 100,
    "60m": 200,
    "1d": 700,
}

# 跨合约拼接最小窗口年龄（天）：9999 主力连续/分期主力拼接只服务
# 早于此年龄的远期历史；最近月任何请求只回本合约自身 bar。
_STITCH_MIN_AGE_DAYS = 30

# 全局连接池单例（lazy，主事件循环内创建/复用）
_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool | None:
    """获取 tickdata asyncpg 连接池（lazy 单例）。

    未配置 TICKDATA_POSTGRES_HOST 时返回 None（调用方据此跳过本源，降级到 HTTP/PG）。
    首次调用在主事件循环内创建池，后续复用；建立失败本次降级，下次重试。
    load_settings 也在 try 内：测试/无 .env 环境下 Settings 校验异常等同
    未配置（本文件承诺任何异常都不抛上层——2026-08-23 曾在无 .env 的
    部署测试容器里炸过补深路径）。
    """
    global _pool
    if _pool is not None:
        return _pool
    try:
        s = load_settings()
        if not s.tickdata_postgres_host:
            return None
        _pool = await asyncpg.create_pool(
            host=s.tickdata_postgres_host,
            port=s.tickdata_postgres_port,
            user=s.tickdata_postgres_user,
            password=s.tickdata_postgres_password,
            database=s.tickdata_postgres_db,
            min_size=1,
            max_size=8,
            command_timeout=10,
            max_inactive_connection_lifetime=300,
        )
        logger.info(
            "TickData 连接池已建立 %s:%s/%s",
            s.tickdata_postgres_host,
            s.tickdata_postgres_port,
            s.tickdata_postgres_db,
        )
    except Exception:
        logger.warning("TickData 连接池建立失败，本次降级到 HTTP/PG", exc_info=True)
        _pool = None
    return _pool


def _format_bar_time(ts: datetime, period: str) -> str:
    """把 pg-tickdata ts 映射成现系统 bar.time 字符串格式。

    现系统约定（见 services/kline.py）：
    - 日线："YYYY-MM-DD"
    - 分钟线："YYYY-MM-DD HH:MM:SS"
    pg-tickdata ts 为 naive 北京时间 timestamp(3)，直接格式化即可。
    """
    if period == "1d":
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _parse_end_time(end_time: str) -> datetime | None:
    """解析现系统 end_time 字符串为 naive datetime（北京时间）。

    兼容分钟线 "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DD HH:MM" 与日线
    "YYYY-MM-DD"。分钟精度（无秒）游标来自分发源链（vvtr/新浪）的 bar
    time——翻页跨源边界时客户端会把无秒游标带进 tickdata 深历史路径；
    此处不兼容会解析成 None，历史翻页页退化成"最新视图"语义，被新鲜
    度守卫整页拦截（2026-09-01 rb9999 15m 翻页停在批次边界即此路径）。
    口径与 services/kline.py::_parse_bar_clock 对齐。
    """
    t = str(end_time or "").strip()
    if not t:
        return None
    for fmt, span in (
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y-%m-%d", 10),
    ):
        if len(t) < span:
            continue
        try:
            return datetime.strptime(t[:span], fmt)
        except ValueError:
            continue
    return None


def _product_code(instrument: str) -> str:
    """提取品种代码：rb2610 -> rb, ma2609 -> ma, if2606 -> if"""
    return "".join(c for c in instrument if c.isalpha())


async def _query_tickdata_rows(pool, pg_period: int, instrument: str, start_dt, end_dt, n: int):
    """查单合约 K线 rows（DESC）。失败返回 []。

    timeout=60：深历史分区（2009 年代月分区按 instrument 裁剪慢，2026-09-01
    实测 rb0909 单段查询 >10s）会撞 asyncpg 默认 10s 命令超时——分期拼接
    的各段独立降级为 []，页面在 2009 边界静默变空。显式放宽到与日历查询
    一致的 60s；近段热路径查询本就秒级返回，timeout 只是上限不增加时延。
    """
    try:
        async with pool.acquire() as conn:
            if end_dt is not None:
                return await conn.fetch(
                    "SELECT ts, open, high, low, close, volume, position "
                    "FROM kline "
                    "WHERE period = $1 AND instrument = $2 AND ts >= $3 AND ts < $4 "
                    "ORDER BY ts DESC LIMIT $5",
                    pg_period, instrument, start_dt, end_dt, n,
                    timeout=60,
                )
            return await conn.fetch(
                "SELECT ts, open, high, low, close, volume, position "
                "FROM kline "
                "WHERE period = $1 AND instrument = $2 AND ts >= $3 "
                "ORDER BY ts DESC LIMIT $4",
                pg_period, instrument, start_dt, n,
                timeout=60,
            )
    except Exception:
        logger.warning(
            "TickData 查询失败 instrument=%s period=%s，降级",
            instrument, pg_period, exc_info=True,
        )
        return []


def _rows_to_bars(rows, period: str, instrument: str = "") -> list[dict[str, Any]]:
    """rows -> 标准 bar dict（升序，eob→bob 起点标签，同键折叠）。"""
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    for r in reversed(rows):  # 查询为 DESC，反转为升序
        ts = _eob_to_bob(r["ts"], period, instrument)
        bar = {
            "time": _format_bar_time(ts, period),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": int(r["volume"]) if r["volume"] is not None else 0,
            "settle": None,
            "open_interest": int(r["position"]) if r["position"] is not None else None,
        }
        if out and out[-1]["time"] == bar["time"]:
            # 同起点键折叠：vvtr 的 1m 收盘打印行（eob 23:01 → bob 22:59）
            # 与真实末根同键，收盘价/量并归收盘桶（左闭右开），不覆盖整根。
            prev = out[-1]
            prev["high"] = max(prev["high"], bar["high"])
            prev["low"] = min(prev["low"], bar["low"])
            prev["close"] = bar["close"]
            prev["volume"] += bar["volume"]
            if bar["open_interest"] is not None:
                prev["open_interest"] = bar["open_interest"]
            continue
        out.append(bar)
    return out


def _eob_to_bob(ts: datetime, period: str, instrument: str) -> datetime:
    """pg-tickdata ts 为周期终点（eob）口径 → 现系统起点（bob）口径。

    eob−周期 时刻落在其所属桶内，用交易分钟轴归一到桶起点（收盘打印
    分钟、跨休盘长桶均正确回退）；品种未知/轴外时刻保守回退平移值。
    2026-08-25 前读取未换算：夜盘末根 eob"23:00" 与主链 bob"22:45"
    双标签并存——夜盘缓存整段终点标签、开盘刷新后重建起点标签，
    图面表现为「23:00 的 K 线消失」+ 相邻 bar 错位一格。
    """
    minutes = PERIOD_MAP.get(period)
    if not minutes or minutes >= 1440:
        return ts
    bob = ts - timedelta(minutes=minutes)
    product = _product_code(instrument) if instrument else ""
    if product:
        try:
            from app.services.session_profiles import period_start_for_product

            axis = period_start_for_product(product, bob, minutes)
            if axis is not None:
                return axis
        except Exception:
            logger.debug(
                "eob→bob 轴归一失败 %s %s", instrument, period, exc_info=True
            )
    return bob


# ========== 主力连续换月平滑（back-adjustment） ==========
# 实测（2024 rb9999）：13 次主力切换中，拼接序列收益 vs 新主力自身收益偏差
# 中位数 0.58%、p90 2.01%、最大 2.23% —— 每次换月引入 0.4~1.5 倍日波动的虚假
# 盈亏，close-to-close 回测被系统性污染。读时做后向乘法平滑（不动数据库）。

_ROLL_CAL_TTL = 7 * 24 * 3600
# product+span -> (loaded_at_monotonic, [(d0_iso, factor)])
# 按 bars 自身跨度查日历：跨整个序列的常数乘子对收益无影响，只有序列
# 内部的换月才产生虚假跳变；20 年全量日历要扫 240 个月分区，容易撞
# 连接池 10s command_timeout（实测 15m 深拼接与分钟平滑均因此静默失效）。
_roll_cal_cache: dict[tuple[str, str, str], tuple[float, list[tuple[str, float]]]] = {}


async def _load_roll_calendar(
    conn, product: str, d_from: str | None = None, d_to: str | None = None
) -> list[tuple[str, float]]:
    """换月日历：[(切换前交易日 d0, f=new(d0)/old(d0))]，按 d0 升序（含缓存）

    d_from/d_to 限定日历跨度（ISO 日期，闭开区间）；None 时退回 20 年全量。
    """
    import time as _time

    key = (product, d_from or "", d_to or "")
    now = _time.monotonic()
    cached = _roll_cal_cache.get(key)
    if cached and now - cached[0] < _ROLL_CAL_TTL:
        return cached[1]
    from datetime import date as _date

    def _as_date(s: str | None):
        if not s:
            return None
        try:
            return _date.fromisoformat(str(s)[:10])
        except ValueError:
            return None

    if d_from and d_to:
        rows = await conn.fetch(
            "SELECT ts, instrument, volume, close FROM kline "
            "WHERE period = 1440 AND instrument LIKE $1 "
            "AND right(instrument, 4) NOT IN ('9999', '8888') "
            "AND ts >= $2 AND ts < $3 "
            "ORDER BY ts",
            product + "%", _as_date(d_from), _as_date(d_to),
            timeout=60,
        )
    else:
        rows = await conn.fetch(
            "SELECT ts, instrument, volume, close FROM kline "
            "WHERE period = 1440 AND instrument LIKE $1 "
            "AND right(instrument, 4) NOT IN ('9999', '8888') "
            "AND ts >= current_date - interval '20 years' "
            "ORDER BY ts",
            product + "%",
            timeout=60,
        )
    rolls = detect_rolls(rows)
    _roll_cal_cache[key] = (now, rolls)
    return rolls


def detect_rolls(rows: list) -> list[tuple[str, float]]:
    """从日线 rows（ts/instrument/volume/close）检测换月点。

    dominant = 当日成交量最大的具体合约；相邻交易日 dominant 变化即换月。
    f 用同一天 d0 两合约各自收盘价（同日双挂，无 9999 依赖）。
    """
    dom: dict[Any, tuple[str, int]] = {}
    close_at: dict[tuple[Any, str], float] = {}
    for r in rows:
        d, ins = r["ts"].date(), r["instrument"]
        vol = int(r["volume"] or 0)
        if d not in dom or vol > dom[d][1]:
            dom[d] = (ins, vol)
        if r["close"] is not None:
            close_at[(d, ins)] = float(r["close"])
    days = sorted(dom)
    rolls: list[tuple[str, float]] = []
    for i in range(1, len(days)):
        d0 = days[i - 1]
        old_ins, new_ins = dom[d0][0], dom[days[i]][0]
        if old_ins == new_ins:
            continue
        c_old = close_at.get((d0, old_ins))
        c_new = close_at.get((d0, new_ins))
        if c_old and c_new and c_old > 0:
            rolls.append((d0.isoformat(), c_new / c_old))
    return rolls


def _bar_trading_day(t: str) -> str:
    """bar 时间串 → 交易日（夜盘 21:00 后归属次一自然日的交易日）"""
    d = t[:10]
    hhmm = t[11:16] if len(t) >= 16 else ""
    if hhmm >= "21:00":
        try:
            from datetime import date as _date, timedelta as _td

            return (_date.fromisoformat(d) + _td(days=1)).isoformat()
        except ValueError:
            return d
    return d


def _apply_backward_adjustment(
    bars: list[dict[str, Any]], rolls: list[tuple[str, float]]
) -> list[dict[str, Any]]:
    """后向乘法平滑：bar(t) 的价格 ×= Π(发生在 t 之后所有换月的 f)

    换月处收益因此等于新主力自身收益；调整只改 OHLC 绝对值（历史价
    被重述为新合约口径，回溯调整序列的固有特性），量/仓不变。
    分钟 bar 按交易日归属判定新旧侧：夜盘（≥21:00）归属次一交易日——
    若按自然日前缀比较，换月日夜盘会被错误留在旧侧，边界残留 1/f
    量级的跳变（实测 ma 2026-04-15 换月残留 +11.3% 即此 artifact）。
    """
    if not bars or not rolls:
        return bars
    suffix = [1.0] * (len(rolls) + 1)
    for i in range(len(rolls) - 1, -1, -1):
        suffix[i] = suffix[i + 1] * rolls[i][1]
    out: list[dict[str, Any]] = []
    ri = 0
    for b in bars:
        t = _bar_trading_day(str(b.get("time") or ""))
        while ri < len(rolls) and rolls[ri][0] < t:
            ri += 1
        m = suffix[ri]
        if m != 1.0:
            b = dict(b)
            for k in ("open", "high", "low", "close"):
                v = b.get(k)
                if v is not None:
                    b[k] = float(v) * m
        out.append(b)
    return out


async def _maybe_adjust(
    pool,
    bars: list[dict[str, Any]],
    product: str,
    instrument: str,
    stitched: bool,
    own_first_time: str | None = None,
) -> list[dict[str, Any]]:
    """含主力连续段的序列做换月平滑；失败静默返回原序列（不阻塞行情）

    own_first_time：序列中"目标合约自有数据"最早 bar 时间（合并前的首查
    结果）。自有数据段（序列后缀）内部价格本就连续，其后的换月因子
    一律不适用——否则双重平滑会在自有段起点制造 f 倍假跳变（实测
    ma 2026-04-15 残留 +10.9% 即此 artifact）。None = 无自有段（纯
    9999/拼接序列），全部换月适用（与日线口径一致）。
    """
    if not bars or instrument[-4:] == "8888":
        return bars
    if not stitched and instrument[-4:] != "9999":
        return bars
    try:
        # 日历跨度 = 序列自身日期范围（只平滑序列内部换月；外部换月对
        # 全序列是常数乘子，对收益/特征无影响，省掉 20 年全量扫描）
        d_from = str(bars[0].get("time") or "")[:10]
        d_to = str(bars[-1].get("time") or "")[:10]
        rolls: list[tuple[str, float]] = []
        if d_from and len(d_from) == 10 and d_to and len(d_to) == 10:
            async with pool.acquire() as conn:
                rolls = await _load_roll_calendar(conn, product, d_from, d_to)
        else:
            async with pool.acquire() as conn:
                rolls = await _load_roll_calendar(conn, product)
        if own_first_time:
            own_day = _bar_trading_day(str(own_first_time)[:19])
            rolls = [(d0, f) for d0, f in rolls if d0 < own_day]
        return _apply_backward_adjustment(bars, rolls)
    except Exception:
        logger.debug("换月平滑失败 %s（返回未平滑序列）", product, exc_info=True)
        return bars




# ── 新鲜度守卫 ──────────────────────────────────────────────
# tickdata 由外部批次灌入（CSV 导入，上游断供时库会滞后数周）。
# 近期锚定的请求若最新 bar 落后超过宽限期，视为未命中 → 返回空降级到
# 实时源（新浪/PG），绝不拿数月前的数据冒充近期（否则回测/挖掘会
# 静默用到过期行情）。历史区间请求（end 距今较远）不受影响——
# 它们要的是当年覆盖，与库的最新时间无关。
_STALE_GRACE_DAYS = 7
_RECENT_ANCHOR_DAYS = 14


def _recent_anchored(end_dt) -> bool:
    """请求是否锚定近期（无 end_time=取最新，或 end 距今 ≤2 周宽限）"""
    if end_dt is None:
        return True
    now = datetime.now(_BJ_TZ).replace(tzinfo=None)
    return (now - end_dt).days <= _RECENT_ANCHOR_DAYS


def _bars_stale(bars: list[dict[str, Any]], end_dt) -> bool:
    """最新 bar 距请求锚点是否超过宽限期"""
    if not bars:
        return False
    ref = end_dt if end_dt is not None else datetime.now(_BJ_TZ).replace(tzinfo=None)
    last = _parse_end_time(str(bars[-1].get("time") or ""))
    if last is None:
        return False
    return (ref - last).days > _STALE_GRACE_DAYS



def _segment_consecutive(daily_rows: list) -> list[list]:
    """按日主力行 [(ts, instrument)] → 连续同合约段 [[ins, first_day, last_day]]"""
    segs: list[list] = []
    for r in daily_rows:
        ins, ts = r["instrument"], r["ts"]
        if segs and segs[-1][0] == ins:
            segs[-1][2] = ts
        else:
            segs.append([ins, ts, ts])
    return segs


async def _fetch_dominant_minute(
    pool,
    product: str,
    period: str,
    start_dt,
    end_bound,
    n: int,
) -> list[dict[str, Any]]:
    """分期主力分钟拼接：窗口内按日成交量最大的具体合约逐段取分钟线

    背景：9999 主力连续的分钟历史只从上游批次起点（2025-10）开始，
    但各具体合约的分钟数据早就有。窗口早期 9999 缺失时，用当期真实
    主力合约现场拼出连续序列（段边界 = 主力切换日；段前后各留 1 天
    夜盘缓冲，重叠由调用方按 time 去重）。
    段间换月跳空由调用方 adjusted=True 时统一做后向平滑（日线换月日历）；
    日线不走本函数（9999 日线历史足够深）。
    """
    pg_period = PERIOD_MAP.get(period)
    if not pg_period or pg_period == 1440 or not product:
        return []
    end_ref = end_bound or datetime.now(_BJ_TZ).replace(tzinfo=None)
    try:
        async with pool.acquire() as conn:
            daily = await conn.fetch(
                "SELECT ts, instrument FROM ("
                " SELECT ts, instrument, volume,"
                " row_number() OVER (PARTITION BY ts ORDER BY volume DESC) rn"
                " FROM kline WHERE period = 1440 AND instrument LIKE $1"
                " AND right(instrument, 4) NOT IN ('9999', '8888')"
                " AND ts >= $2 AND ts < $3) t"
                " WHERE rn = 1 ORDER BY ts",
                product + "%", start_dt, end_ref,
                timeout=60,  # 深回溯跨多月分区，默认 10s 会超时静默丢拼接段
            )
    except Exception:
        # warning 而非 debug：这里吞掉后该页翻页会在批次边界终止，
        # 必须在默认日志级别可见（2026-09-01 rb9999 排查时的教训）
        logger.warning(
            "分期主力日历查询失败 %s %s~%s，本页拼接段丢弃",
            product, start_dt, end_ref, exc_info=True,
        )
        return []
    out: list[dict[str, Any]] = []
    for ins, d0, d1 in _segment_consecutive(daily):
        seg_rows = await _query_tickdata_rows(
            pool, pg_period, ins,
            d0 - timedelta(days=1),  # 夜盘归属前一交易日
            d1 + timedelta(days=1),
            n,
        )
        out.extend(_rows_to_bars(seg_rows, period, ins))
    out.sort(key=lambda b: str(b.get("time") or ""))
    seen: set[str] = set()
    dedup: list[dict[str, Any]] = []
    for b in out:
        t = str(b.get("time") or "")
        if t and t not in seen:
            seen.add(t)
            dedup.append(b)
    return dedup


async def earliest_minute_date(product: str, period: str) -> str | None:
    """该品种在 tickdata 里分钟数据的最早日期（YYYY-MM-DD）

    具体合约与主力连续（9999/8888）共用此边界：连续合约的批次灌入段
    较浅，但分期主力拼接会接管到具体合约起点（如 rb→2009），深回溯
    两类符号最终可回溯深度一致。
    未配置/查询失败返回 None（调用方退回通用文案）。
    """
    pg_period = PERIOD_MAP.get(period)
    if pg_period is None or pg_period == 1440:
        return None
    pool = await _get_pool()
    if pool is None:
        return None
    # 逐年探测（ts 边界触发分区裁剪；无界 LIKE min 会跨 264 分区超时）。
    # 单次年份探测在深历史分区上要数十秒（2026-09-01 实测 25~45s/次），
    # 线性扫 2005 起最多 22 次不可行 → 按年二分（数据自上市年起逐年
    # 存在，单调性成立；个别整年断供洞会略晚报早界，仅影响提示文案）。
    try:
        from datetime import date as _date

        this_year = datetime.now(_BJ_TZ).year
        async with pool.acquire() as conn:

            async def _year_min(y: int):
                r = await conn.fetchrow(
                    "SELECT min(ts) mn FROM kline WHERE period=$1 AND instrument LIKE $2"
                    " AND ts >= $3 AND ts < $4",
                    pg_period, product + "%",
                    _date(y, 1, 1), _date(y + 1, 1, 1),
                )
                return r["mn"] if r else None

            lo, hi = 2005, this_year
            while lo < hi:
                mid = (lo + hi) // 2
                if await _year_min(mid) is not None:
                    hi = mid
                else:
                    lo = mid + 1
            mn = await _year_min(lo)
            if mn is not None:
                return mn.date().isoformat()
            return None
    except Exception:
        return None


async def freshest_contracts(
    product: str, period: str, k: int = 3, recent_days: int = 30
) -> list[str]:
    """该品种最近 N 天在库内有数据的具体合约（按最新 ts 降序，取前 k）

    用途：静态 ALL_CONTRACTS 每品种只有一条（可能已过期，如 cu2607 停在
    2026-07-15）——分钟深历史源需要"当前还在更新"的合约时用它取候选。
    ts 限定近 N 天做分区裁剪，避免全表扫描。失败/未配置返回空。
    """
    pg_period = PERIOD_MAP.get(period)
    product = (product or "").strip().lower()
    if pg_period is None or not product:
        return []
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT instrument, max(ts) mx FROM kline "
                "WHERE period = $1 AND instrument ~ $2 "
                "AND right(instrument, 4) NOT IN ('9999', '8888') "
                "AND ts >= current_date - $3 "
                "GROUP BY instrument ORDER BY mx DESC LIMIT $4",
                pg_period, f"^{product}\\d{{4}}$", recent_days, k,
                timeout=30,
            )
            return [r["instrument"] for r in rows]
    except Exception:
        logger.debug("freshest_contracts 查询失败 %s", product, exc_info=True)
        return []


async def fetch_tickdata_kline(
    symbol: str,
    period: str,
    end_time: str = "",
    limit: int = 200,
    adjusted: bool = False,
) -> list[dict[str, Any]]:
    """从 pg-tickdata 读取历史 K线（具体合约 + 主力连续 fallback）。

    选 rb2610 拖动历史：rb2610 上市后用 rb2610；上市前自动拼接主力连续 rb9999
    （rb9999 是各时期主力拼接的连续序列，近期=当前主力），实现"拖到 2009"的长历史。

    Args:
        symbol: 合约代码（小写完整合约，如 rb2610）
        period: "1m"/"5m"/"15m"/"30m"/"60m"/"1d"
        end_time: 懒加载游标 ts < end_time；空串取最近 limit 根
        limit: 返回根数，<=0 按 500
        adjusted: 日线且含主力连续段时做换月后向平滑（回测/因子评估应传 True，
                  K 线图展示保持 False 以显示真实历史价）

    Returns:
        标准 bar dict 列表（升序）。未配置/失败/无数据均返回 []（降级）。
    """
    pg_period = PERIOD_MAP.get(period)
    if pg_period is None:
        return []
    pool = await _get_pool()
    if pool is None:
        return []
    instrument = str(symbol or "").strip().lower()
    if not instrument:
        return []
    n = limit if (limit and limit > 0) else 500
    end_dt = _parse_end_time(end_time)
    # lookback 按 limit 动态（长历史回测需大窗口覆盖品种最早），保底 _LOOKBACK_DAYS。
    # 注意：大 lookback 会跨多月分区，单次查询变慢，但回测是一次性加载，可接受。
    if period == "1d":
        _lb_days = n * 1.5 + 30
    else:
        _per_day = {"1m": 240, "5m": 48, "15m": 16, "30m": 8, "60m": 4}.get(period, 240)
        _lb_days = n / _per_day * 1.5 + 30
    lookback = timedelta(days=max(_lb_days, _LOOKBACK_DAYS.get(period, 60)))
    start_dt = (end_dt if end_dt is not None else datetime.now(_BJ_TZ).replace(tzinfo=None)) - lookback

    own_bars = _rows_to_bars(
        await _query_tickdata_rows(pool, pg_period, instrument, start_dt, end_dt, n),
        period,
        instrument,
    )
    bars = own_bars

    # 主力连续 fallback：仅限远期历史回溯——窗口锚点（end_dt）早于
    # _STITCH_MIN_AGE_DAYS 天才允许拼接。最近月的任何请求（最新视图/
    # 近期回溯）一律禁止：9999 近段=当前主力合约，时间戳新鲜、价格却是
    # 另一合约（2026-08-21 ma2701 15m 被 ma2609 价格污染即此路径），
    # 时间型 stale gate 拦不住跨合约错价。最近月只回本合约自身 bar，
    # 数据不足/为空由上层降级到分发源等实时源。
    _now_naive = datetime.now(_BJ_TZ).replace(tzinfo=None)
    # 2026-09-02 修复:历史回溯(end_time 非空)放开锚点限制。原逻辑近端锚点
    # (<_STITCH_MIN_AGE_DAYS 天)只回本合约自身 bar、不足 limit 不拼接 →
    # 上层 has_more=len(bars)>=limit 判 false → 桌面端分页回溯在自身段头
    # 断链(rb2701 60m 449 根<500 即断,回测只能拿到近几个月)。拼接按 time
    # 去重、自身 bar 优先、仅补缺口段;最新视图(end_time 空)仍被 end_dt
    # is None 挡住,跨合约污染防护不变。
    stitch_allowed = bool(end_dt is not None)
    stitched = False
    if stitch_allowed and len(bars) < n:
        product = _product_code(instrument)
        cont = product + "9999"
        if product and cont != instrument:
            cont_bars = _rows_to_bars(
                await _query_tickdata_rows(pool, pg_period, cont, start_dt, end_dt, n),
                period,
                cont,
            )
            if cont_bars:
                stitched = True
                seen = {b["time"] for b in bars}
                for b in cont_bars:
                    if b["time"] not in seen:
                        bars.append(b)
                        seen.add(b["time"])
                bars.sort(key=lambda x: x["time"])
                bars = bars[-n:]

    # 分期主力拼接：同样仅限远期历史回溯（同上，最近月禁跨合约拼接）。
    # 窗口内不足 n 根即存在缺口。_fetch_dominant_minute 每段取"最新 n 根"，
    # 必须把拼接窗口夹到真正缺的区间才拿得到：
    # - 尾部洞：own 最新 bar 距游标 > 2 天（own 段止于批次边界）→ 补
    #   [last_ts, end_dt)——rb9999 游标 07-02 只回 202 根短页即此；
    # - 头部缺口：first_ts 距 start_dt > 2 天 → 补 [start_dt, first_ts)。
    #   头部补齐一轮后 first_ts 会继续前移（历史数据密度不均，如 rb2505
    #   2025 年 1-2 月仅日盘 16 根/天、3 月起日盘+夜盘 22 根/天，单段
    #   newest-500 只够覆盖到 3 月）——循环补到页满或无新数据。
    # 与已有 bar 按 time 去重合并，整体取最新 n 根。
    if stitch_allowed and period != "1d" and len(bars) < n:
        product = _product_code(instrument)

        def _merge(dom: list[dict[str, Any]]) -> int:
            seen = {b["time"] for b in bars}
            fresh = [b for b in dom if b["time"] not in seen]
            if fresh:
                bars.extend(fresh)
                bars.sort(key=lambda x: x["time"])
                bars[:] = bars[-n:]
            return len(fresh)

        # 尾洞一次性补（own 段与游标之间的批次边界洞）
        last_ts = _parse_end_time(str(bars[-1]["time"])) if bars else None
        if (
            last_ts is not None and end_dt is not None
            and (end_dt - last_ts).total_seconds() > 2 * 86400
        ):
            dom = await _fetch_dominant_minute(pool, product, period, last_ts, end_dt, n)
            if dom and _merge(dom):
                stitched = True
        # 头部缺口循环补（每轮按当前 first_ts 重新夹窗）
        for _ in range(3):
            if len(bars) >= n:
                break
            first_ts = _parse_end_time(str(bars[0]["time"])) if bars else None
            if (
                first_ts is not None
                and (first_ts - start_dt).total_seconds() <= 2 * 86400
            ):
                break
            dom = await _fetch_dominant_minute(
                pool, product, period, start_dt, first_ts or end_dt, n
            )
            if not dom or not _merge(dom):
                break
            stitched = True

    # 新鲜度守卫：近期锚定 + 库内最新 bar 过期 → 整体视为未命中，
    # 降级实时源（调用方 get_kline_history 已有多级降级链）
    if bars and _recent_anchored(end_dt) and _bars_stale(bars, end_dt):
        logger.warning(
            "TickData 数据过期（最新 %s，锚点 %s），降级实时源 %s %s",
            bars[-1].get("time"), end_dt or "now", instrument, period,
        )
        return []

    # 换月平滑：日线（9999 主力连续/拼接）与分钟（分期主力拼接）同用
    # 日线换月日历做后向乘法平滑。分钟拼接段边界存在换月跳空——不平滑
    # 会给 close-to-close 回测/因子评估注入每次换月 0.4~1.5 倍日波动的
    # 虚假盈亏（见 _apply_backward_adjustment 实测注释）。
    # 日期粒度为交易日归属（夜盘算次一交易日）：换月日夜盘属新主力侧。
    if adjusted:
        product = _product_code(instrument) or instrument.rstrip("0123456789")
        # 自有数据起点：合并前的首查结果（目标合约自身的最早 bar）。
        # 自有段内部价格连续，其后的换月因子不适用（防双重平滑）。
        # 例外：请求符号本身是 9999 连续序列时全部换月适用（日线既有口径）。
        own_first: str | None = None
        if instrument[-4:] != "9999" and own_bars:
            own_first = str(own_bars[0].get("time") or "") or None
        bars = await _maybe_adjust(
            pool, bars, product, instrument, stitched, own_first_time=own_first
        )
    return bars


async def close_tickdata_pool() -> None:
    """关闭 tickdata 连接池（应用退出时调用，可选）。"""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None
