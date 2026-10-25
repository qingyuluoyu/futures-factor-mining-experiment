"""扩展信息源 —— 期限结构(基差代理)/ 板块动量 / 跨周期(分钟挂日线、日线挂日内统计)

纯 OHLCV 单品种特征在封存期基准上整体无样本外 alpha(见 scripts/bench-mining.py),
本模块引入单品种 K 线之外的信息。设计要点:

- **随 bar 携带**:外部信息预先算成逐 bar 的原始值,以 `x_*` 字段写进 bar dict
  (与 open_interest 同一先例)。feature_matrix(bars) 签名不变,walk-forward 切片、
  封存段、GPU mine_features、实盘 factor_np 全部自动一致——切 bars 即切信息源。
- **分组 + 键存在即启用**:attach_* 给**每根** bar 写入本组全部键(无值写 None),
  所以任意切片的首根 bar 都能判定本组是否启用,特征矩阵行数在各段间恒定。
- **因果**:日频信息挂到日线 bar 时用当日收盘后的值(pnl 口径为 pos[t]×close[t→t+1],
  当日收盘可见);挂到分钟 bar 时只用**当前交易日之前**已完成交易日的值
  (夜盘按 trading_day_of_bar_time 归属次一交易日),当日开盘价除外(已发生)。
- 本模块只做纯计算,不做 IO;数据由调用方(基准脚本 / 桌面端数据层)加载后传入。

真现货基差无数据源(tickdata 库与服务端均无现货价),以近远月价差/展期收益率代理。
"""

from __future__ import annotations

import math
from bisect import bisect_left
from typing import Any, Iterable

from app.services.trading_hours import trading_day_of_bar_time

# (特征名, bar 字段, 组) —— 顺序即特征 id 顺序(features.py 追加在 id 40 起),append-only
AUX_FEATURES: tuple[tuple[str, str, str], ...] = (
    # 期限结构(基差代理):年化展期收益率,正值 = 远月升水(contango)
    ("TS_SLOPE", "x_ts_slope", "ts"),
    ("TS_MAIN2", "x_ts_main2", "ts"),
    ("TS_NEAR", "x_ts_near", "ts"),
    ("TS_SLOPE_CHG5", "x_ts_slope_chg5", "ts"),
    ("TS_SLOPE_CHG20", "x_ts_slope_chg20", "ts"),
    # 板块动量(同 product_sectors 板块、剔除自身,等权)
    ("SEC_RET5", "x_sec_ret5", "sec"),
    ("SEC_RET20", "x_sec_ret20", "sec"),
    ("REL_RET20", "x_rel_ret20", "sec"),
    ("SEC_BREADTH", "x_sec_breadth", "sec"),
    # 分钟线挂日线趋势(仅分钟周期):上一完成交易日的日线结构 + 当日开盘以来
    ("D_RET5", "x_d_ret5", "dtr"),
    ("D_RET20", "x_d_ret20", "dtr"),
    ("D_MA_DIFF20", "x_d_ma_diff20", "dtr"),
    ("D_CHAN_POS", "x_d_chan_pos", "dtr"),
    ("ID_RET", "x_id_ret", "dtr"),
    # 日线挂日内统计(仅日线周期):由当日分钟线算出
    ("ID_RVOL", "x_id_rvol", "ids"),
    ("ID_EFF", "x_id_eff", "ids"),
    ("ID_LATE_RET", "x_id_late_ret", "ids"),
    ("ID_NIGHT_RET", "x_id_night_ret", "ids"),
    ("ID_DAY_RET", "x_id_day_ret", "ids"),
)

AUX_GROUPS: dict[str, tuple[str, ...]] = {}
for _name, _key, _group in AUX_FEATURES:
    AUX_GROUPS.setdefault(_group, ())
    AUX_GROUPS[_group] = AUX_GROUPS[_group] + (_key,)

AUX_GROUP_TEXT: dict[str, str] = {
    "ts": "期限结构",
    "sec": "板块动量",
    "dtr": "日线趋势",
    "ids": "日内统计",
}

# 年化展期收益率截断:临近交割时月差很小,年化会放大到失真
_CARRY_CLIP = 2.0
# 期限结构只看有流动性的合约:持仓量 ≥ 最大持仓的该比例
_MIN_OI_SHARE = 0.02
# 两合约到期差小于该月数时不算年化价差(除零/噪声)
_MIN_TAU_GAP = 0.5


def aux_groups_present(bars: list[dict[str, Any]]) -> tuple[str, ...]:
    """bars 启用了哪些信息源组(按首根 bar 的键判定,attach_* 保证每根都写键)"""
    if not bars:
        return ()
    first = bars[0]
    return tuple(g for g, keys in AUX_GROUPS.items() if keys[0] in first)


def _day(bar: dict[str, Any]) -> str:
    return trading_day_of_bar_time(str(bar.get("time") or ""))


def _is_minute(bars: list[dict[str, Any]]) -> bool:
    """分钟 bar 判定:时间串带时刻且不全是 00:00"""
    for b in bars[:50]:
        t = str(b.get("time") or "")
        if len(t) >= 16 and t[11:16] != "00:00":
            return True
    return False


def _write(bars: list[dict[str, Any]], group: str, values: list[dict[str, float] | None]) -> list[dict[str, Any]]:
    """复制 bars 并写入本组全部键(不改入参)"""
    keys = AUX_GROUPS[group]
    out = []
    for bar, v in zip(bars, values):
        nb = dict(bar)
        for k in keys:
            nb[k] = None if v is None else v.get(k)
        out.append(nb)
    return out


def _map_days(
    bars: list[dict[str, Any]],
    by_day: dict[str, dict[str, float]],
    minute: bool,
) -> list[dict[str, float] | None]:
    """日频值 → 逐 bar。日线:取 ≤ 当日的最近值;分钟:取 < 当前交易日的最近值"""
    days = sorted(by_day)
    out: list[dict[str, float] | None] = []
    for bar in bars:
        d = _day(bar)
        if not d:
            out.append(None)
            continue
        i = bisect_left(days, d)
        if not minute and i < len(days) and days[i] == d:
            out.append(by_day[days[i]])
        elif i > 0:
            out.append(by_day[days[i - 1]])
        else:
            out.append(None)
    return out


# ── 期限结构 ────────────────────────────────────────────────────


def _contract_month(contract: str, trade_day: str) -> tuple[int, int] | None:
    """合约代码 → (年, 月)。rb2510 → (2025,10);郑商所三位 ta510 按交易日推断年代"""
    digits = "".join(ch for ch in contract if ch.isdigit())
    try:
        ty = int(trade_day[:4])
    except ValueError:
        return None
    if len(digits) == 4:
        year, month = 2000 + int(digits[:2]), int(digits[2:])
    elif len(digits) == 3:
        year = ty - ty % 10 + int(digits[0])
        if year < ty - 1:
            year += 10
        elif year > ty + 8:
            year -= 10
        month = int(digits[1:])
    else:
        return None
    if not 1 <= month <= 12:
        return None
    return year, month


def _carry(p_near: float, tau_near: float, p_far: float, tau_far: float) -> float | None:
    gap = tau_far - tau_near
    if abs(gap) < _MIN_TAU_GAP or p_near <= 0 or p_far <= 0:
        return None
    v = math.log(p_far / p_near) / (gap / 12.0)
    return max(-_CARRY_CLIP, min(_CARRY_CLIP, v))


def term_structure_by_day(
    curve: dict[str, Iterable[tuple[str, float, float]]],
) -> dict[str, dict[str, float]]:
    """逐交易日期限结构。curve: {交易日: [(合约代码, 收盘, 持仓量), ...]}

    - TS_SLOPE:持仓加权回归 ln(价格) ~ 到期年数 的斜率(年化展期收益率)
    - TS_MAIN2:持仓最大两合约间的年化价差(主力/次主力)
    - TS_NEAR:最近两个流动合约的年化价差(近端结构,最接近现货基差的代理)
    - TS_SLOPE_CHG5/20:斜率 5/20 个交易日变化(升贴水加深或收敛)
    """
    out: dict[str, dict[str, float]] = {}
    slopes: list[float] = []
    for day in sorted(curve):
        try:
            tday = int(day[8:10])
        except ValueError:
            continue
        # 同一交割月去重(郑商所同一合约可能同时以 ta605/ta2605 两种代码出现),留持仓大者
        by_month: dict[tuple[int, int], tuple[float, float]] = {}
        for contract, close, oi in curve[day]:
            if not close or close <= 0 or not oi or oi <= 0:
                continue
            ym = _contract_month(contract, day)
            if ym is None:
                continue
            if ym not in by_month or float(oi) > by_month[ym][1]:
                by_month[ym] = (float(close), float(oi))
        pts: list[tuple[float, float, float]] = []  # (tau 月, 价格, 持仓)
        for (y, m), (close, oi) in by_month.items():
            months = (y * 12 + m) - (int(day[:4]) * 12 + int(day[5:7]))
            if months < 0:
                continue
            # 合约约在交割月中旬到期
            tau = months + 0.5 - (tday - 1) / 30.0
            pts.append((tau, close, oi))
        if len(pts) < 2:
            continue
        max_oi = max(p[2] for p in pts)
        pts = sorted(p for p in pts if p[2] >= max_oi * _MIN_OI_SHARE)
        if len(pts) < 2:
            continue
        # 加权最小二乘斜率(按年)
        w = [p[2] for p in pts]
        x = [p[0] / 12.0 for p in pts]
        y = [math.log(p[1]) for p in pts]
        sw = sum(w)
        mx = sum(wi * xi for wi, xi in zip(w, x)) / sw
        my = sum(wi * yi for wi, yi in zip(w, y)) / sw
        sxx = sum(wi * (xi - mx) ** 2 for wi, xi in zip(w, x))
        if sxx < 1e-9:
            continue
        slope = sum(wi * (xi - mx) * (yi - my) for wi, xi, yi in zip(w, x, y)) / sxx
        slope = max(-_CARRY_CLIP, min(_CARRY_CLIP, slope))
        by_oi = sorted(pts, key=lambda p: -p[2])
        a, b = sorted(by_oi[:2])
        main2 = _carry(a[1], a[0], b[1], b[0])
        near = _carry(pts[0][1], pts[0][0], pts[1][1], pts[1][0])
        prev = out[max(out)] if out else None
        slopes.append(slope)
        out[day] = {
            "x_ts_slope": slope,
            "x_ts_main2": main2 if main2 is not None else (prev or {}).get("x_ts_main2", 0.0),
            "x_ts_near": near if near is not None else (prev or {}).get("x_ts_near", 0.0),
            "x_ts_slope_chg5": slope - slopes[-6] if len(slopes) > 5 else 0.0,
            "x_ts_slope_chg20": slope - slopes[-21] if len(slopes) > 20 else 0.0,
        }
    return out


def attach_term_structure(
    bars: list[dict[str, Any]], ts_by_day: dict[str, dict[str, float]]
) -> list[dict[str, Any]]:
    return _write(bars, "ts", _map_days(bars, ts_by_day, _is_minute(bars)))


# ── 板块动量 ────────────────────────────────────────────────────


def _daily_closes(daily_bars: list[dict[str, Any]]) -> tuple[list[str], list[float]]:
    days: list[str] = []
    closes: list[float] = []
    for b in daily_bars:
        d = _day(b)
        c = float(b.get("close") or 0)
        if not d or c <= 0:
            continue
        if days and d == days[-1]:
            closes[-1] = c
            continue
        days.append(d)
        closes.append(c)
    return days, closes


def _momentum_stats(daily_bars: list[dict[str, Any]]) -> dict[str, tuple[float, float, float]]:
    """{交易日: (5日收益, 20日收益, 是否在 20 日均线上方 ±1)}"""
    days, closes = _daily_closes(daily_bars)
    out: dict[str, tuple[float, float, float]] = {}
    for i, d in enumerate(days):
        c = closes[i]
        r5 = c / closes[i - 5] - 1.0 if i >= 5 else 0.0
        r20 = c / closes[i - 20] - 1.0 if i >= 20 else 0.0
        win = closes[max(0, i - 19) : i + 1]
        above = 1.0 if c > sum(win) / len(win) else -1.0
        out[d] = (r5, r20, above)
    return out


def sector_by_day(
    own_daily: list[dict[str, Any]],
    peers_daily: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, float]]:
    """逐交易日板块动量(伙伴等权,只用当日有 bar 的伙伴)

    伙伴序列应为无换月跳空的连续序列(指数/复权主连);未复权主连的换月
    跳空会污染伙伴收益,调用方负责选择数据口径。
    """
    own = _momentum_stats(own_daily)
    peers = [_momentum_stats(b) for b in peers_daily.values() if b]
    out: dict[str, dict[str, float]] = {}
    for d in sorted(own):
        vals = [p[d] for p in peers if d in p]
        if not vals:
            continue
        r5 = sum(v[0] for v in vals) / len(vals)
        r20 = sum(v[1] for v in vals) / len(vals)
        out[d] = {
            "x_sec_ret5": r5,
            "x_sec_ret20": r20,
            "x_rel_ret20": own[d][1] - r20,
            "x_sec_breadth": sum(v[2] for v in vals) / len(vals),
        }
    return out


def attach_sector(
    bars: list[dict[str, Any]], sec_by_day: dict[str, dict[str, float]]
) -> list[dict[str, Any]]:
    return _write(bars, "sec", _map_days(bars, sec_by_day, _is_minute(bars)))


# ── 分钟线挂日线趋势 ────────────────────────────────────────────


def attach_daily_trend(
    minute_bars: list[dict[str, Any]], daily_bars: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """给分钟 bar 挂上一完成交易日的日线结构 + 当日开盘以来收益

    daily_bars 与 minute_bars 须为同一价格口径(同一合约,或同一复权主连)。
    """
    days, closes = _daily_closes(daily_bars)
    highs: dict[str, float] = {}
    lows: dict[str, float] = {}
    for b in daily_bars:
        d = _day(b)
        if d:
            highs[d] = float(b.get("high") or b.get("close") or 0)
            lows[d] = float(b.get("low") or b.get("close") or 0)
    stats: list[tuple[float, float, float, float, float]] = []  # r5, r20, ma20, hh20, ll20
    for i, d in enumerate(days):
        c = closes[i]
        lo = max(0, i - 19)
        win = closes[lo : i + 1]
        hh = max(highs.get(x, c) for x in days[lo : i + 1])
        ll = min(lows.get(x, c) for x in days[lo : i + 1])
        stats.append(
            (
                c / closes[i - 5] - 1.0 if i >= 5 else 0.0,
                c / closes[i - 20] - 1.0 if i >= 20 else 0.0,
                sum(win) / len(win),
                hh,
                ll,
            )
        )
    values: list[dict[str, float] | None] = []
    cur_day = ""
    day_open = 0.0
    for bar in minute_bars:
        d = _day(bar)
        c = float(bar.get("close") or 0)
        if d != cur_day:
            cur_day = d
            day_open = float(bar.get("open") or c)
        i = bisect_left(days, d) - 1 if d else -1
        if i < 0 or c <= 0:
            values.append(None)
            continue
        r5, r20, ma, hh, ll = stats[i]
        values.append(
            {
                "x_d_ret5": r5,
                "x_d_ret20": r20,
                "x_d_ma_diff20": c / ma - 1.0 if ma > 0 else 0.0,
                "x_d_chan_pos": (c - ll) / (hh - ll) * 2.0 - 1.0 if hh > ll else 0.0,
                "x_id_ret": c / day_open - 1.0 if day_open > 0 else 0.0,
            }
        )
    return _write(minute_bars, "dtr", values)


# ── 日线挂日内统计 ──────────────────────────────────────────────


def _clock(bar: dict[str, Any]) -> int | None:
    t = str(bar.get("time") or "")
    if len(t) < 16:
        return None
    try:
        return int(t[11:13]) * 60 + int(t[14:16])
    except ValueError:
        return None


def intraday_stats_by_day(minute_bars: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """由分钟线算逐交易日日内统计(当日收盘后可见)

    - ID_RVOL:日内已实现波动 sqrt(Σ r²)(对数收益,含首根 open→close)
    - ID_EFF:日内路径效率 ln(收/开) / Σ|r| ∈ [-1,1](趋势日 → ±1,震荡日 → 0)
    - ID_LATE_RET:尾盘最后一小时收益(按 bar 间隔推算根数,与时间标签起止口径无关)
    - ID_NIGHT_RET / ID_DAY_RET:夜盘段(前收 → 夜盘末)与日盘段(夜盘末 → 收)收益
    """
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for b in minute_bars:
        d = _day(b)
        if not d or float(b.get("close") or 0) <= 0:
            continue
        if groups and groups[-1][0] == d:
            groups[-1][1].append(b)
        else:
            groups.append((d, [b]))
    out: dict[str, dict[str, float]] = {}
    prev_close = 0.0
    for d, day_bars in groups:
        closes = [float(b["close"]) for b in day_bars]
        first_open = float(day_bars[0].get("open") or closes[0]) or closes[0]
        rets = [math.log(closes[0] / first_open)] + [
            math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
        ]
        abs_sum = sum(abs(r) for r in rets)
        clocks = [_clock(b) for b in day_bars]
        gaps = sorted(
            clocks[i] - clocks[i - 1]
            for i in range(1, len(clocks))
            if clocks[i] is not None and clocks[i - 1] is not None and clocks[i] > clocks[i - 1]
        )
        gap = gaps[len(gaps) // 2] if gaps else 60
        k = max(1, round(60 / max(gap, 1)))
        late_base = closes[-1 - k] if len(closes) > k else first_open
        night_idx = [
            i for i, m in enumerate(clocks) if m is not None and (m >= 21 * 60 or m < 3 * 60)
        ]
        base = prev_close if prev_close > 0 else first_open
        if night_idx:
            night_close = closes[night_idx[-1]]
            night_ret = math.log(night_close / base)
            day_ret = math.log(closes[-1] / night_close)
        else:
            night_ret = 0.0
            day_ret = math.log(closes[-1] / base)
        out[d] = {
            "x_id_rvol": math.sqrt(sum(r * r for r in rets)),
            "x_id_eff": math.log(closes[-1] / first_open) / abs_sum if abs_sum > 1e-12 else 0.0,
            "x_id_late_ret": math.log(closes[-1] / late_base),
            "x_id_night_ret": night_ret,
            "x_id_day_ret": day_ret,
        }
        prev_close = closes[-1]
    return out


def attach_intraday_stats(
    daily_bars: list[dict[str, Any]], minute_bars: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """给日线 bar 挂当日日内统计;无分钟数据的交易日写 None(特征层前向填充)"""
    stats = intraday_stats_by_day(minute_bars)
    values = [stats.get(_day(b)) for b in daily_bars]
    return _write(daily_bars, "ids", values)


def aggregate_daily(minute_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """分钟线按交易日聚合为日线(time=交易日;夜盘归次一交易日)

    用于「分钟线挂日线趋势」时保证日线与分钟线同一价格口径(同合约/同复权)。
    """
    out: list[dict[str, Any]] = []
    for b in minute_bars:
        d = _day(b)
        c = float(b.get("close") or 0)
        if not d or c <= 0:
            continue
        if out and out[-1]["time"] == d:
            day = out[-1]
            day["high"] = max(day["high"], float(b.get("high") or c))
            day["low"] = min(day["low"], float(b.get("low") or c))
            day["close"] = c
            day["volume"] += float(b.get("volume") or 0)
            day["open_interest"] = b.get("open_interest")
        else:
            out.append(
                {
                    "time": d,
                    "open": float(b.get("open") or c),
                    "high": float(b.get("high") or c),
                    "low": float(b.get("low") or c),
                    "close": c,
                    "volume": float(b.get("volume") or 0),
                    "open_interest": b.get("open_interest"),
                }
            )
    return out
