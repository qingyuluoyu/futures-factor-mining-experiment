"""AI 合成 K 线 · 真实历史片段种子（微观结构校准）

生成合成 K 线前，随机抽取该品种一段真实历史 K 线作为「种子」，提取
微观结构统计，用于两处：

1. 剧本提示词：把种子片段的统计摘要 + 尾部走势喂给 LLM，要求剧本在
   种子的波动率/跳空量级内演化，而非凭空想象；
2. 渲染器校准：波动率聚集（对数波动率 AR(1)，持续性取自种子 |收益|
   自相关）、时段边界跳空（隔夜/午休开盘相对昨收的 gap 分布）、
   成交量分布与引线比例全部改用种子标定值，替换全局默认。

本项目不内置任何行情历史源——历史数据由使用者自行接入，两种方式：

1. CSV 目录（零代码）：把该品种的历史 K 线放到
   ``$QIHUO_SYNTHETIC_SEED_CSV_DIR``（默认 ``data/kline``）下的
   ``{symbol}_{timeframe}.csv``（表头 time,open,high,low,close,volume，
   升序），生成时会自动随机抽取其中一段做种子；
2. Provider 钩子（代码接入）：``register_history_provider(async_fn)``，
   ``async_fn(symbol, timeframe)`` 返回该品种完整历史（升序 dict 列表，
   键同 CSV 列），可对接自己的数据库/行情 API。

未接入或数据不足时静默回退纯合成路径，不影响生成主链路。
"""

from __future__ import annotations

import csv
import logging
import math
import os
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Awaitable, Callable

logger = logging.getLogger("qihuo.synthetic")

# 判定种子可用的最少根数：低于此统计不可信，回退纯合成
MIN_SEED_BARS = 80
# 提示词附带的种子尾部根数
PROMPT_TAIL_BARS = 24
# CSV 种子目录（相对 backend 工作目录，可用环境变量覆盖）
CSV_DIR_ENV = "QIHUO_SYNTHETIC_SEED_CSV_DIR"
CSV_DIR_DEFAULT = "data/kline"

# 历史数据 provider：(symbol, timeframe) → 完整历史 bars（升序）/ None
HistoryProvider = Callable[[str, str], "Awaitable[list[dict[str, Any]] | None]"]

_provider: HistoryProvider | None = None


def register_history_provider(fn: HistoryProvider) -> None:
    """注册自定义历史 K 线 provider（数据库/行情 API 等自建数据源）。

    fn(symbol, timeframe) 返回该品种完整历史（升序，键
    time/open/high/low/close/volume），失败抛异常或返回 None/空均可——
    种子链路会静默回退，不会阻断合成生成。
    """
    global _provider
    _provider = fn


@dataclass
class HistorySeedProfile:
    """从真实历史段标定的微观结构统计（渲染器与提示词共用）"""

    bars: list[dict[str, Any]]  # 种子片段（升序，原样保留供提示词引用）
    symbol: str
    timeframe: str
    start: str  # 首根时间
    end: str  # 末根时间
    ref_price: float  # 末根收盘（合成起点价）
    amplitude: float  # 单根振幅中位数 (high-low)/open
    bar_sigma: float  # 单根对数收益标准差
    vol_persistence: float  # |收益| 一阶自相关 → 波动率聚集强度
    vol_of_vol: float  # EWMA 波动率对数标准差
    gap_mean: float  # 方向性跳空对数均值（时段边界）
    gap_std: float  # 方向性跳空对数标准差
    gap_rate: float  # 时段边界出现方向性跳空的比例
    vol_median: float  # 成交量中位数
    vol_log_sigma: float  # 成交量对数正态噪声强度（P20/P80 离散度）
    vol_edge_factor: float  # 日内首末时段相对中位的量能放大
    wick_per_body_med: float  # 引线/实体比中位
    wick_per_body_hi: float  # 引线/实体比 P80
    range_frac_p99: float  # 单根振幅 P99（流动性约束：单根上限）
    decimals: int  # 价格小数位（真实 tick 精度）


# ------------------------------------------------------------------
# 基础工具
# ------------------------------------------------------------------

def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_bar_time(t: str) -> datetime | None:
    t = str(t or "").strip()
    if not t:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[:19], fmt)
        except ValueError:
            continue
    return None


def is_session_boundary(prev_t: datetime, cur_t: datetime, timeframe: str) -> bool:
    """两根相邻 bar 之间是否跨交易时段边界（会产生开盘跳空）。

    日线：相邻即隔夜边界；分钟线：跨日（隔夜/周末）或同日跨午休
    （上午 ≤11:30 → 下午 ≥13:00）。合成时间轴只有日盘，与真实
    日盘种子段的边界定义保持一致。
    """
    if timeframe == "1d":
        return True
    if prev_t.date() != cur_t.date():
        return True
    return prev_t.time() <= time(11, 30) and cur_t.time() >= time(13, 0)


def _lag1_autocorr(xs: list[float]) -> float:
    n = len(xs)
    if n < 8:
        return 0.0
    mean = sum(xs) / n
    num = sum((xs[i] - mean) * (xs[i + 1] - mean) for i in range(n - 1))
    den = sum((x - mean) ** 2 for x in xs)
    if den <= 1e-18:
        return 0.0
    return num / den


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _quantile(sorted_xs: list[float], q: float) -> float:
    """线性插值分位数（输入须已升序）"""
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = _clamp(q, 0.0, 1.0) * (len(sorted_xs) - 1)
    lo_i = int(pos)
    hi_i = min(lo_i + 1, len(sorted_xs) - 1)
    frac = pos - lo_i
    return sorted_xs[lo_i] * (1 - frac) + sorted_xs[hi_i] * frac


def _decimals_of(prices: list[float]) -> int:
    """从真实价格推断小数位（tick 精度），上限 2"""
    best = 0
    for p in prices[:200]:
        s = f"{p:.4f}".rstrip("0")
        if s.endswith("."):
            s = s[:-1]
        parts = s.split(".")
        best = max(best, len(parts[1]) if len(parts) > 1 else 0)
    return min(best, 2)


# ------------------------------------------------------------------
# 统计标定
# ------------------------------------------------------------------

def build_seed_profile(
    bars: list[dict[str, Any]],
    timeframe: str,
    *,
    symbol: str = "",
) -> HistorySeedProfile | None:
    """从真实历史段提取微观结构统计；数据不足/非法返回 None"""
    valid: list[tuple[datetime, dict[str, Any]]] = []
    for b in bars or []:
        t = _parse_bar_time(str(b.get("time") or ""))
        o, h, l, c = _f(b.get("open")), _f(b.get("high")), _f(b.get("low")), _f(b.get("close"))
        if t is None or min(o, h, l, c) <= 0 or h < max(o, c) or l > min(o, c):
            continue
        valid.append((t, b))
    if len(valid) < MIN_SEED_BARS:
        return None
    valid.sort(key=lambda x: x[0])

    opens = [_f(b.get("open")) for _, b in valid]
    highs = [_f(b.get("high")) for _, b in valid]
    lows = [_f(b.get("low")) for _, b in valid]
    closes = [_f(b.get("close")) for _, b in valid]
    vols = [max(0.0, _f(b.get("volume"))) for _, b in valid]

    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    bar_sigma = max(1e-5, statistics.pstdev(rets)) if len(rets) >= 8 else 0.008

    # 波动率聚集：|收益| 一阶自相关（持续性）+ EWMA 波动率的对数离散（vol-of-vol）
    abs_rets = [abs(r) for r in rets]
    vol_persistence = _clamp(_lag1_autocorr(abs_rets), 0.0, 0.95)
    ewma_v = 0.0
    ewma_series: list[float] = []
    for r in rets:
        ewma_v = r * r if not ewma_series else 0.94 * ewma_v + 0.06 * r * r
        ewma_series.append(math.sqrt(max(ewma_v, 1e-12)))
    log_ewma = [math.log(v) for v in ewma_series[5:]]
    vol_of_vol = _clamp(
        statistics.pstdev(log_ewma) if len(log_ewma) >= 8 else 0.25, 0.05, 0.9
    )

    # 跳空：时段边界 open 相对昨收；只统计方向性 gap（>2 tick），
    # 无方向的边界（open≈昨收）不参与均值/标准差，只计入 gap_rate 分母
    decimals = _decimals_of(opens + closes)
    tick = 10 ** (-decimals) if decimals > 0 else 1.0
    boundary_flags = [False] * len(valid)
    gaps_dir: list[float] = []
    boundary_cnt = 0
    for i in range(1, len(valid)):
        boundary_flags[i] = is_session_boundary(valid[i - 1][0], valid[i][0], timeframe)
        if not boundary_flags[i]:
            continue
        boundary_cnt += 1
        g = math.log(opens[i] / closes[i - 1])
        if abs(opens[i] - closes[i - 1]) > 2 * tick:
            gaps_dir.append(g)
    if gaps_dir:
        gap_mean = statistics.fmean(gaps_dir)
        gap_std = max(1e-4, statistics.pstdev(gaps_dir))
        gap_rate = len(gaps_dir) / max(1, boundary_cnt)
    else:
        gap_mean, gap_std, gap_rate = 0.0, 5e-4, 0.0
    gap_std = _clamp(gap_std, 1e-4, 0.08)

    # 量能：中位数、对数离散（P20/P80 → 对数正态 σ）、首末时段放大
    pos_vols = [v for v in vols if v > 0]
    vol_median = statistics.median(pos_vols) if pos_vols else 2000.0
    sv = sorted(pos_vols)
    p20, p80 = _quantile(sv, 0.2), _quantile(sv, 0.8)
    if p20 > 0 and p80 > p20:
        vol_log_sigma = _clamp(math.log(p80 / p20) / 2.562, 0.1, 1.0)
    else:
        vol_log_sigma = 0.3
    edge_idx = [i for i in range(len(valid)) if boundary_flags[i]]
    edge_idx.append(len(valid) - 1)  # 末根必是当日尾盘
    edge_vols = [vols[i] for i in edge_idx if vols[i] > 0]
    if edge_vols and vol_median > 0:
        vol_edge_factor = _clamp(statistics.median(edge_vols) / vol_median, 1.0, 5.0)
    else:
        vol_edge_factor = 1.0

    # 引线/实体比例（上下两侧合并统计）
    wick_ratios: list[float] = []
    for i in range(len(valid)):
        body = abs(closes[i] - opens[i])
        eff = max(body, tick)
        wick_ratios.append((highs[i] - max(opens[i], closes[i])) / eff)
        wick_ratios.append((min(opens[i], closes[i]) - lows[i]) / eff)
    wick_ratios = [max(0.0, w) for w in wick_ratios]
    wick_ratios.sort()
    wick_med = _clamp(statistics.median(wick_ratios), 0.05, 1.5)
    wick_hi = _clamp(_quantile(wick_ratios, 0.8), wick_med + 0.02, 2.5)

    # 单根振幅：中位（提示词的「日内振幅」）与 P99（单根上限=流动性约束）
    range_fracs = sorted((highs[i] - lows[i]) / opens[i] for i in range(len(valid)))
    amplitude = _clamp(statistics.median(range_fracs), 0.0005, 0.2)
    range_frac_p99 = _clamp(_quantile(range_fracs, 0.99), amplitude * 1.2, 0.15)

    return HistorySeedProfile(
        bars=[b for _, b in valid],
        symbol=symbol,
        timeframe=timeframe,
        start=str(valid[0][1].get("time") or ""),
        end=str(valid[-1][1].get("time") or ""),
        ref_price=closes[-1],
        amplitude=amplitude,
        bar_sigma=bar_sigma,
        vol_persistence=vol_persistence,
        vol_of_vol=vol_of_vol,
        gap_mean=gap_mean,
        gap_std=gap_std,
        gap_rate=gap_rate,
        vol_median=vol_median,
        vol_log_sigma=vol_log_sigma,
        vol_edge_factor=vol_edge_factor,
        wick_per_body_med=wick_med,
        wick_per_body_hi=wick_hi,
        range_frac_p99=range_frac_p99,
        decimals=decimals,
    )


# ------------------------------------------------------------------
# 历史数据接入：provider 钩子 / CSV 目录
# ------------------------------------------------------------------

def _seed_csv_dir() -> str:
    return os.environ.get(CSV_DIR_ENV, CSV_DIR_DEFAULT)


def load_seed_bars_from_csv(
    symbol: str, timeframe: str, *, csv_dir: str | None = None,
) -> list[dict[str, Any]] | None:
    """从 CSV 目录读取该品种完整历史（升序）。

    文件名 ``{symbol}_{timeframe}.csv``（symbol 归一小写），表头
    time,open,high,low,close,volume；time 为 YYYY-MM-DD[ HH:MM:SS]。
    文件不存在/解析失败返回 None（静默回退纯合成）。
    """
    path = os.path.join(csv_dir or _seed_csv_dir(), f"{symbol.lower()}_{timeframe}.csv")
    if not os.path.isfile(path):
        return None
    bars: list[dict[str, Any]] = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fp:
            for row in csv.DictReader(fp):
                t = str(row.get("time") or row.get("date") or "").strip()
                if not t or _parse_bar_time(t) is None:
                    continue
                o, h, l = _f(row.get("open")), _f(row.get("high")), _f(row.get("low"))
                c, v = _f(row.get("close")), _f(row.get("volume"))
                if min(o, h, l, c) <= 0:
                    continue
                bars.append({
                    "time": t, "open": o, "high": h,
                    "low": l, "close": c, "volume": int(v),
                })
    except (OSError, csv.Error) as exc:
        logger.warning("种子 CSV 读取失败(%s): %s", path, exc)
        return None
    if not bars:
        return None
    bars.sort(key=lambda b: str(b["time"]))
    return bars


def pick_random_window(
    bars: list[dict[str, Any]], num_bars: int, rng: random.Random,
) -> list[dict[str, Any]] | None:
    """从完整历史中随机抽取一段种子（升序连续切片）"""
    n = len(bars)
    if n < MIN_SEED_BARS:
        return None
    want = max(MIN_SEED_BARS, min(n, num_bars + 120))
    start = rng.randint(0, n - want)
    return bars[start:start + want]


async def resolve_seed_segment(
    symbol: str,
    timeframe: str,
    num_bars: int,
    rng: random.Random,
) -> list[dict[str, Any]] | None:
    """解析种子历史段：注册的 provider 优先，其次 CSV 目录。

    两种来源都返回该品种的完整历史，再随机抽段；都不可用返回 None。
    任何异常静默吞掉——种子是增强项，绝不阻断合成主链路。
    """
    if not symbol:
        return None
    history: list[dict[str, Any]] | None = None
    if _provider is not None:
        try:
            history = await _provider(symbol.lower(), timeframe)
        except Exception as exc:
            logger.warning("种子 provider 调用失败(%s %s): %s", symbol, timeframe, exc)
            history = None
    if not history:
        try:
            history = load_seed_bars_from_csv(symbol, timeframe)
        except Exception as exc:
            logger.warning("种子 CSV 加载失败(%s %s): %s", symbol, timeframe, exc)
            history = None
    if not history:
        return None
    return pick_random_window(history, num_bars, rng)


# ------------------------------------------------------------------
# 提示词摘要
# ------------------------------------------------------------------

def _compact_bar(b: dict[str, Any], timeframe: str) -> str:
    t = str(b.get("time") or "")
    seg = t[5:10] if timeframe == "1d" else t[5:16]
    return (
        f"{seg} O{_f(b.get('open')):g} H{_f(b.get('high')):g} "
        f"L{_f(b.get('low')):g} C{_f(b.get('close')):g} V{_f(b.get('volume')):.0f}"
    )


def summarize_seed_for_prompt(profile: HistorySeedProfile) -> str:
    """种子片段的提示词摘要：统计概览 + 尾部走势 + 演化约束"""
    bars = profile.bars
    o0 = _f(bars[0].get("open"))
    c1 = _f(bars[-1].get("close"))
    chg = (c1 / o0 - 1) * 100 if o0 > 0 else 0.0
    tf_label = {"1d": "日线", "60m": "60分钟", "30m": "30分钟",
                "15m": "15分钟", "5m": "5分钟", "1m": "1分钟"}.get(
                    profile.timeframe, profile.timeframe)
    lines = [
        f"【真实历史种子片段 · {profile.symbol or '品种'} {tf_label}】",
        f"区间 {profile.start} ~ {profile.end}，共 {len(bars)} 根："
        f"首开 {o0:g} → 末收 {c1:g}（{chg:+.1f}%）；单根振幅中位 "
        f"{profile.amplitude * 100:.2f}%，单根波动 {profile.bar_sigma * 1e4:.0f}bp；"
        f"时段跳空率 {profile.gap_rate * 100:.0f}%"
        + (
            f"（幅度 {profile.gap_mean * 1e4:+.0f}±{profile.gap_std * 1e4:.0f}bp）"
            if profile.gap_rate > 0 else ""
        )
        + f"；成交量中位 {profile.vol_median:.0f}。",
        "种子尾部走势（time open high low close volume）：",
        *(_compact_bar(b, profile.timeframe) for b in bars[-PROMPT_TAIL_BARS:]),
        "要求：剧本描述种子片段「之后」的新走势——波动率与跳空量级和种子一致"
        "（±50% 内演化），趋势形态可以不同，但必须像该品种真实行情；禁止复述种子已走过的路径。",
    ]
    return "\n".join(lines)
