"""AI 合成 K 线生成 + 边推边决策推进器

设计要点：
1. LLM 只产出**结构化市场剧本**（趋势/波动率/跳跃事件），不直接输出 OHLCV 数字。
   这样单次 LLM 调用即可，质量稳定、可生成任意根数。
2. 后端用 **几何布朗运动 (GBM) + 跳跃扩散** 把剧本程序化渲染成具体 OHLCV，
   贴合所选品种的真实价格量级、日内振幅、合约乘数。
3. 接入了历史数据（CSV 目录或 register_history_provider，见 synthetic_seed）
   时先随机抽取该品种一段真实历史 K 线作**种子**，剧本提示词与渲染器都按
   种子校准——波动率聚集、时段跳空、量能分布等微观结构来自真实行情而非
   理想化假设；未接入/失败回退纯合成。
4. step 推进器无状态：每根 K 线收盘时由前端回传"已可见前缀 + 账户快照"，
   后端重建窗口与 VirtualAccount 后调用既有策略引擎，回传决策与最新账户快照。
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.backtest.account import VirtualAccount
from app.services.backtest.data import TIMEFRAME_MAX_DAYS
from app.services.backtest.signals import apply_hard_rules, normalize_action, quant_signal
from app.services.backtest.synthetic_seed import (
    HistorySeedProfile,
    build_seed_profile,
    is_session_boundary,
    resolve_seed_segment,
    summarize_seed_for_prompt,
)
from app.services.paper.specs import resolve_trade_params

logger = logging.getLogger("qihuo.synthetic")

# 各周期每交易日的 K 线根数（按国内期货日盘 09:00-11:30/13:30-15:00 = 270 分钟）
BARS_PER_DAY: dict[str, int] = {
    "1d": 1,
    "60m": 4,
    "30m": 8,
    "15m": 16,
    "5m": 48,
    "1m": 240,
}

# 各周期默认日内振幅（无品种行情参考时）
DEFAULT_AMPLITUDE: dict[str, float] = {
    "1d": 0.018,
    "60m": 0.012,
    "30m": 0.010,
    "15m": 0.008,
    "5m": 0.005,
    "1m": 0.003,
}

# 品种代码 → 默认参考价（无行情时按量级给一个合理价）
_DEFAULT_REF_PRICES: dict[str, float] = {
    "RB": 3600.0, "HC": 3900.0, "I": 820.0, "J": 2200.0, "JM": 1300.0,
    "RU": 13500.0, "CU": 70000.0, "AL": 19500.0, "ZN": 23000.0, "PB": 16500.0,
    "NI": 135000.0, "SN": 230000.0, "AU": 550.0, "AG": 7000.0,
    "A": 4200.0, "M": 3200.0, "Y": 8000.0, "P": 7600.0, "C": 2500.0,
    "CS": 2900.0, "PP": 7500.0, "L": 8000.0, "V": 6000.0, "TA": 5800.0,
    "MA": 2500.0, "SR": 6200.0, "CF": 14000.0, "OI": 8000.0, "RM": 2500.0,
    "FG": 1500.0, "SF": 6500.0, "SM": 6500.0, "AP": 8000.0, "CJ": 13000.0,
    "UR": 1800.0, "SA": 1500.0, "PF": 6800.0, "SH": 2500.0, "EC": 2000.0,
    "WH": 2500.0, "RI": 2600.0, "LR": 2600.0, "PM": 2300.0, "JR": 2900.0,
    "FU": 3000.0, "BU": 3600.0, "SC": 600.0, "LU": 3500.0, "NR": 10000.0,
    "BC": 48000.0, "BB": 130.0, "FB": 3500.0, "WR": 4000.0,
}

MAX_BARS = 5000


@dataclass
class MarketScript:
    """LLM 产出的市场剧本（结构化参数）"""

    regime: str = "volatile_range"
    trend_strength: float = 0.4
    volatility: float = 0.015
    drift_bps: float = 0.0
    jumps: list[dict[str, Any]] = field(default_factory=list)
    volume_pattern: str = "trending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "trend_strength": round(self.trend_strength, 3),
            "volatility": round(self.volatility, 4),
            "drift_bps": round(self.drift_bps, 2),
            "jumps": self.jumps,
            "volume_pattern": self.volume_pattern,
        }


@dataclass
class SyntheticSeries:
    """一次合成生成的完整结果"""

    bars: list[dict[str, Any]]
    script: MarketScript
    meta: dict[str, Any]


# ------------------------------------------------------------------
# 剧本生成（调 LLM）
# ------------------------------------------------------------------

_REGIME_DESC = {
    "bull": "单边上涨，回撤浅",
    "bear": "单边下跌，反弹弱",
    "sideways": "区间震荡，无方向",
    "bull_then_sideways": "先涨后盘整",
    "bear_then_bounce": "先跌后反弹",
    "volatile_range": "宽幅震荡，波动大",
}

_PROMPT_SYSTEM = """你是期货市场剧本设计师。请输出一个 JSON 对象描述一段合成行情的「市场剧本」，供后端程序化随机过程生成 K 线。
只输出 JSON，禁止任何解释文字。字段约束：
- regime: 字符串，必须从 ["bull","bear","sideways","bull_then_sideways","bear_then_bounce","volatile_range"] 中选一个
- trend_strength: 0.0-1.0，趋势强度
- volatility: 0.005-0.05，日内波动率（相对基准价）
- drift_bps: -50 到 50，漂移（基点，正为多头漂移）
- jumps: 数组，0-4 个跳跃事件，每个元素 {"at": 0.0-1.0 位置比例, "direction": "up"|"down", "size": 0.01-0.08}
- volume_pattern: 字符串，从 ["trending","high_open_low_mid","spiky","fading"] 中选一个
要求剧本合理、有变化、避免极端单一方向。"""


def _build_user_prompt(
    *, symbol: str, symbol_name: str, timeframe: str, num_bars: int,
    ref_price: float, amplitude: float,
    seed_summary: str | None = None,
) -> str:
    tf_label = {"1d": "日线", "60m": "60分钟", "30m": "30分钟",
                "15m": "15分钟", "5m": "5分钟", "1m": "1分钟"}.get(timeframe, timeframe)
    sym_label = f"{symbol_name}({symbol})" if symbol_name else (symbol or "通用品种")
    prompt = (
        f"品种：{sym_label}，参考价约 {ref_price:.0f}，周期：{tf_label}，"
        f"日内振幅约 {amplitude*100:.1f}%，需生成约 {num_bars} 根 K 线。"
        f"请在该品种的价格量级和振幅范围内设计剧本。"
    )
    if seed_summary:
        # 有真实历史种子时，统计约束以种子为准（上文 amplitude 即种子标定值）
        prompt = prompt + "\n\n" + seed_summary
    return prompt


async def generate_market_script(
    session: AsyncSession,
    user_id: UUID,
    model_row_id: str,
    *,
    symbol: str,
    symbol_name: str,
    timeframe: str,
    num_bars: int,
    ref_price: float,
    amplitude: float,
    seed_profile: HistorySeedProfile | None = None,
) -> MarketScript:
    """调 LLM 产出市场剧本；失败回退到默认随机剧本。

    seed_profile 非空时：提示词附带真实历史种子摘要，剧本量级被
    种子统计约束（volatility 收敛区间的 amp 即种子振幅中位）。
    """
    from app.services.factor_lab.llm.llm_http import chat_once, extract_json_object

    messages = [
        {"role": "system", "content": _PROMPT_SYSTEM},
        {"role": "user", "content": _build_user_prompt(
            symbol=symbol, symbol_name=symbol_name, timeframe=timeframe,
            num_bars=num_bars, ref_price=ref_price, amplitude=amplitude,
            seed_summary=summarize_seed_for_prompt(seed_profile) if seed_profile else None,
        )},
    ]
    try:
        mid = UUID(str(model_row_id))
        raw = await chat_once(session, user_id, mid, messages, temperature=0.9, max_tokens=600)
    except Exception as exc:
        logger.warning("合成剧本 LLM 调用失败，回退默认剧本: %s", exc)
        return _default_script(timeframe, seed_profile=seed_profile)

    data = extract_json_object(raw)
    if data is None:
        logger.warning("合成剧本 LLM 输出非 JSON，回退默认剧本")
        return _default_script(timeframe, seed_profile=seed_profile)
    return _coerce_script(data, timeframe, amp=amplitude)


def _default_script(
    timeframe: str,
    *,
    seed_profile: HistorySeedProfile | None = None,
) -> MarketScript:
    """LLM 不可用时的默认剧本（带随机 regime）。

    有种子时波动率/跳跃直接取种子统计，保证回退路径也具备真实量级。
    """
    rng = random.Random()
    regime = rng.choice(list(_REGIME_DESC.keys()))
    if seed_profile is not None:
        amp = seed_profile.amplitude
        vol = round(min(amp * 2.0, max(amp * 0.3, seed_profile.bar_sigma)), 4)
        jump_size = round(
            min(0.08, max(0.01, abs(seed_profile.gap_mean) + 2 * seed_profile.gap_std)), 3
        )
    else:
        amp = DEFAULT_AMPLITUDE.get(timeframe, 0.015)
        vol = round(amp * 0.8, 4)
        jump_size = round(rng.uniform(0.015, 0.04), 3)
    return MarketScript(
        regime=regime,
        trend_strength=round(rng.uniform(0.3, 0.7), 3),
        volatility=vol,
        drift_bps=round(rng.uniform(-20, 20), 2),
        jumps=[
            {"at": round(rng.random(), 2),
             "direction": rng.choice(["up", "down"]),
             "size": jump_size}
            for _ in range(rng.randint(1, 2))
        ],
        volume_pattern=rng.choice(["trending", "high_open_low_mid", "spiky", "fading"]),
    )


def _coerce_script(
    data: dict[str, Any], timeframe: str, *, amp: float | None = None
) -> MarketScript:
    """把 LLM 输出强制收敛到合法范围（amp 为振幅基准，种子时=种子振幅中位）"""
    valid_regimes = set(_REGIME_DESC.keys())
    valid_vol = {"trending", "high_open_low_mid", "spiky", "fading"}
    if amp is None:
        amp = DEFAULT_AMPLITUDE.get(timeframe, 0.015)

    regime = str(data.get("regime") or "").strip().lower()
    if regime not in valid_regimes:
        regime = "volatile_range"

    def _clip(v: Any, lo: float, hi: float, default: float) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, f))

    jumps: list[dict[str, Any]] = []
    raw_jumps = data.get("jumps") or []
    if isinstance(raw_jumps, list):
        for j in raw_jumps[:4]:
            if not isinstance(j, dict):
                continue
            at = _clip(j.get("at"), 0.0, 1.0, 0.5)
            direction = "up" if str(j.get("direction") or "").lower() != "down" else "down"
            size = _clip(j.get("size"), 0.01, 0.08, 0.02)
            jumps.append({"at": round(at, 3), "direction": direction, "size": round(size, 3)})

    vol_pat = str(data.get("volume_pattern") or "").strip().lower()
    if vol_pat not in valid_vol:
        vol_pat = "trending"

    return MarketScript(
        regime=regime,
        trend_strength=_clip(data.get("trend_strength"), 0.0, 1.0, 0.4),
        volatility=_clip(data.get("volatility"), amp * 0.3, amp * 2.0, amp),
        drift_bps=_clip(data.get("drift_bps"), -50.0, 50.0, 0.0),
        jumps=jumps,
        volume_pattern=vol_pat,
    )


# ------------------------------------------------------------------
# 程序化渲染（GBM + 跳跃扩散）
# ------------------------------------------------------------------

def resolve_ref_price(symbol: str, ref_price: float | None) -> float:
    """确定生成基准价：优先前端传入 → 品种代码默认表 → 通用 5000"""
    if ref_price and ref_price > 0:
        return float(ref_price)
    code = (symbol or "").strip().upper()
    # 去掉数字月份后缀（如 rb2510 → RB）
    letters = "".join(c for c in code if c.isalpha())
    return _DEFAULT_REF_PRICES.get(letters, 5000.0)


def _regime_drift(script: MarketScript, progress: float) -> float:
    """按剧本形态返回每根 bar 的对数漂移（已归一化到 bar 维度）"""
    ts = script.trend_strength
    drift = script.drift_bps / 10000.0  # bps → 比例
    r = script.regime
    if r == "bull":
        return (0.0006 + drift) * ts
    if r == "bear":
        return (-0.0006 + drift) * ts
    if r == "bull_then_sideways":
        return (0.0008 if progress < 0.5 else 0.00005) * ts + drift * 0.3
    if r == "bear_then_bounce":
        return (-0.0008 if progress < 0.55 else 0.0006) * ts + drift * 0.3
    if r == "volatile_range":
        # 在宽幅震荡中给一个缓慢反转漂移
        return math.sin(progress * math.pi * 3) * 0.0004 * ts + drift * 0.2
    # sideways
    return drift * 0.2 + math.sin(progress * math.pi * 2) * 0.0001 * ts


def _jump_impact(script: MarketScript, progress: float, rng: random.Random) -> float:
    """检查当前 progress 是否命中某个跳跃事件，返回对数冲击"""
    total = 0.0
    for j in script.jumps:
        at = float(j.get("at") or 0.5)
        # 命中窗口：在该 progress 的一小段内触发一次
        if abs(progress - at) < 0.005:
            direction = 1.0 if j.get("direction") != "down" else -1.0
            size = float(j.get("size") or 0.02)
            # 跳跃幅度略加随机扰动
            total += direction * size * rng.uniform(0.8, 1.2)
    return total


def _gen_volume(
    script: MarketScript, progress: float, rng: random.Random,
    bar_ret: float, base_vol: int,
    *,
    log_sigma: float = 0.0, edge_factor: float = 1.0, is_edge: bool = False,
) -> int:
    """按 volume_pattern 生成成交量。

    种子校准时：base_vol=种子量中位，噪声从均匀扰动换对数正态
    （宽度=种子 P20/P80 离散度），首末时段按种子标定的 edge_factor 放大。
    """
    pat = script.volume_pattern
    if pat == "trending":
        # 趋势中放大 abs(收益) 对应的量
        scale = 1.0 + min(4.0, abs(bar_ret) * 80)
    elif pat == "high_open_low_mid":
        scale = 2.2 if progress < 0.2 else (0.6 if progress > 0.8 else 1.0)
    elif pat == "spiky":
        scale = rng.choice([0.5, 0.7, 1.0, 3.0, 5.0])
    elif pat == "fading":
        scale = max(0.3, 2.0 - progress * 1.7)
    else:
        scale = 1.0
    if log_sigma > 0:
        noise = rng.lognormvariate(0.0, log_sigma)
    else:
        noise = rng.uniform(0.7, 1.3)
    if is_edge and edge_factor > 1.0:
        noise *= edge_factor
    vol = int(base_vol * scale * noise)
    return max(1, vol)


def _trading_sessions(timeframe: str, num_bars: int) -> list[datetime]:
    """生成期货日盘交易时段的时间戳序列（北京时间）。

    日盘 09:00-11:30 / 13:30-15:00，共 270 分钟。
    """
    if timeframe == "1d":
        # 日线：从今天往前推 num_bars 个交易日（跳过周末）
        out: list[datetime] = []
        d = date.today()
        while len(out) < num_bars:
            if d.weekday() < 5:  # 0-4 = 周一到周五
                out.append(datetime.combine(d, time(15, 0)))
            d -= timedelta(days=1)
        return list(reversed(out))

    # 分钟线：按周期生成时间戳
    minute_step = {"60m": 60, "30m": 30, "15m": 15, "5m": 5, "1m": 1}[timeframe]
    # 日盘分钟序列（每个交易日的交易分钟）
    day_minutes: list[int] = []
    for (h, m) in [(9, 0), (9, 30), (10, 0), (10, 30), (11, 0)]:
        # 09:00-10:15（含）与 10:30-11:30（含）
        pass
    # 构造一个完整交易日的分钟点
    intra: list[time] = []
    # 09:00 - 10:15（休盘前）
    t_cur = time(9, 0)
    end1 = time(10, 15)
    while t_cur <= end1:
        intra.append(t_cur)
        dt = (datetime.combine(date.today(), t_cur) + timedelta(minutes=minute_step)).time()
        t_cur = dt
    # 10:30 - 11:30
    t_cur = time(10, 30)
    end2 = time(11, 30)
    while t_cur <= end2:
        intra.append(t_cur)
        dt = (datetime.combine(date.today(), t_cur) + timedelta(minutes=minute_step)).time()
        t_cur = dt
    # 13:30 - 15:00
    t_cur = time(13, 30)
    end3 = time(15, 0)
    while t_cur <= end3:
        intra.append(t_cur)
        dt = (datetime.combine(date.today(), t_cur) + timedelta(minutes=minute_step)).time()
        t_cur = dt

    if not intra:
        intra = [time(9, 0)]

    out_dt: list[datetime] = []
    d = date.today()
    # 先填满今天剩余，再往前补交易日
    while len(out_dt) < num_bars:
        if d.weekday() < 5:
            for t_obj in reversed(intra):
                out_dt.append(datetime.combine(d, t_obj))
                if len(out_dt) >= num_bars:
                    break
        d -= timedelta(days=1)
    return list(reversed(out_dt[:num_bars]))


def render_bars_from_script(
    script: MarketScript,
    *,
    symbol: str,
    timeframe: str,
    num_bars: int,
    ref_price: float,
    amplitude: float,
    seed: int | None = None,
    seed_profile: HistorySeedProfile | None = None,
) -> list[dict[str, Any]]:
    """用 GBM + 跳跃扩散把剧本渲染成 OHLCV K 线序列。

    seed_profile 非空时按真实历史种子校准微观结构：
    - 波动率聚集：对数波动率 AR(1)，持续性=种子 |收益| 自相关；
    - 跳空：时段边界（隔夜/午休）开盘价按种子 gap 分布跳开；
    - 流动性约束：量能中位/离散度/首末时段放大、引线比例、
      单根振幅 P99 上限、价格小数位全部取种子标定值。
    无种子时保持既有默认行为（兼容旧调用与测试）。
    """
    n = max(1, min(MAX_BARS, int(num_bars)))
    rng = random.Random(seed) if seed is not None else random.Random()

    times = _trading_sessions(timeframe, n)
    sigma = script.volatility  # 日内波动率
    base_vol = 5000  # 基准成交量
    prev_close = ref_price
    bars: list[dict[str, Any]] = []

    # —— 种子校准参数（无种子时全部退化为既有默认）——
    use_seed = seed_profile is not None
    if use_seed:
        assert seed_profile is not None
        # LLM 剧本的 volatility 表达相对种子振幅的倍率（防量级漂移）
        vol_mult = min(2.5, max(0.4, script.volatility / max(seed_profile.amplitude, 1e-9)))
        sigma_base = max(1e-5, seed_profile.bar_sigma * vol_mult)
        phi = min(0.97, max(0.5, seed_profile.vol_persistence))
        sigma_lv = min(0.9, max(0.05, seed_profile.vol_of_vol)) * 0.6
        sigma_lv_sqrt = math.sqrt(max(0.0, 1.0 - phi * phi))
        base_vol = max(1.0, seed_profile.vol_median)
        decimals = seed_profile.decimals
        tick = 10 ** (-decimals) if decimals > 0 else 1.0
        wick_med = seed_profile.wick_per_body_med
        wick_spread = max(0.05, (seed_profile.wick_per_body_hi - wick_med) / 1.5)
        wick_cap = max(0.1, seed_profile.wick_per_body_hi * 1.5)
        ln_v = math.log(sigma_base)
        # 时段边界标记（首根不算）：隔夜/午休开盘会跳空、量能放大
        boundary = [False] * n
        for i in range(1, n):
            boundary[i] = is_session_boundary(times[i - 1], times[i], timeframe)
        gap_cap = max(3 * seed_profile.gap_std, 0.02)

    for i in range(n):
        progress = i / max(1, n - 1)
        # 每根 bar 的对数收益 = 漂移 + 高斯扰动 + 跳跃
        mu = _regime_drift(script, progress)
        if use_seed:
            # 波动率聚集：对数波动率围绕 ln(sigma_base) 做 AR(1)，
            # 持续性 phi 高 → 高波动/低波动成簇出现（真实市场特征）
            ln_v = (
                phi * ln_v + (1 - phi) * math.log(sigma_base)
                + sigma_lv_sqrt * sigma_lv * rng.gauss(0, 1)
            )
            bar_sigma = math.exp(ln_v)
        else:
            bar_sigma = sigma * 0.6  # 单根波动小于日内总波动
        z = rng.gauss(0, 1)
        jump = _jump_impact(script, progress, rng)
        # 时段边界跳空：开盘价相对昨收按种子 gap 分布跳开（概率=gap_rate）
        gap = 0.0
        if use_seed and i > 0 and boundary[i] and rng.random() < seed_profile.gap_rate:
            g = rng.gauss(seed_profile.gap_mean, seed_profile.gap_std)
            gap = min(gap_cap, max(-gap_cap, g))
        open_px = prev_close * math.exp(gap)
        log_ret = mu + bar_sigma * z + jump
        new_close = open_px * math.exp(log_ret)
        # 防止极端值（含跳空后仍以昨收 ±15% 兜底）
        new_close = max(prev_close * 0.85, min(prev_close * 1.15, new_close))

        o = open_px
        c = new_close
        if use_seed:
            # 引线比例取种子标定（相对实体），tick 精度兜底
            body_min = tick * 2
            eff_body = max(abs(c - o), body_min)
            uw = min(wick_cap, max(0.02, rng.gauss(wick_med, wick_spread)))
            lw = min(wick_cap, max(0.02, rng.gauss(wick_med, wick_spread)))
            upper_wick = eff_body * uw + tick * 0.5
            lower_wick = eff_body * lw + tick * 0.5
            hi = max(o, c) + upper_wick
            lo = min(o, c) - lower_wick
            hi = max(hi, max(o, c) + tick)
            lo = min(lo, min(o, c) - tick)
            # 流动性约束：单根总振幅不超过种子 P99（真实市场极少越界）
            max_swing = max(o * seed_profile.range_frac_p99, 4 * bar_sigma * o)
        else:
            # 真实 K 线引线：约为实体的一部分 + 少量随机噪声，明显短于实体
            # 真实期货日线：上下引线合计约占振幅 30-50%，单边引线通常 < 实体一半
            body = abs(c - o)
            body_min = ref_price * 0.0008  # 极小实体的下限，避免除零和退化
            eff_body = max(body, body_min)
            # 上引线：实体 10%-50% + 小幅噪声
            upper_wick = eff_body * rng.uniform(0.10, 0.50) + ref_price * 0.0003 * abs(rng.gauss(0, 1))
            # 下引线：实体 10%-50% + 小幅噪声
            lower_wick = eff_body * rng.uniform(0.10, 0.50) + ref_price * 0.0003 * abs(rng.gauss(0, 1))
            # 跳跃事件可以适度放大引线（但仍是实体量级）
            if abs(jump) > 0:
                upper_wick += eff_body * abs(jump) * 5
                lower_wick += eff_body * abs(jump) * 5
            hi = max(o, c) + upper_wick
            lo = min(o, c) - lower_wick
            # 防御：保证 high >= max(o,c) + 最小tick，low <= min(o,c) - 最小tick
            tick = ref_price * 0.0005
            hi = max(hi, max(o, c) + tick)
            lo = min(lo, min(o, c) - tick)
            # 极端防御：单根总振幅不超过 10%（真实期货日内极少超过）
            max_swing = ref_price * 0.05
        if hi - lo > max_swing:
            center = (o + c) / 2
            hi = center + max_swing / 2
            lo = center - max_swing / 2
            # 重新确保 OHLC 合法
            hi = max(hi, o, c)
            lo = min(lo, o, c)
        is_edge = bool(use_seed and (boundary[i] or i == n - 1 or (i + 1 < n and boundary[i + 1])))
        vol = _gen_volume(
            script, progress, rng, log_ret, base_vol,
            log_sigma=seed_profile.vol_log_sigma if use_seed else 0.0,
            edge_factor=seed_profile.vol_edge_factor if use_seed else 1.0,
            is_edge=is_edge,
        )

        dp = decimals if use_seed else 2
        bars.append({
            "time": _format_time(times[i], timeframe),
            "open": round(o, dp),
            "high": round(hi, dp),
            "low": round(lo, dp),
            "close": round(c, dp),
            "volume": vol,
        })
        prev_close = c

    return bars


def _format_time(dt: datetime, timeframe: str) -> str:
    """统一与现有 K 线时间格式：日线 YYYY-MM-DD；分钟 YYYY-MM-DD HH:MM:SS"""
    if timeframe == "1d":
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------
# 主入口：生成完整合成序列
# ------------------------------------------------------------------

async def generate_synthetic_series(
    session: AsyncSession,
    user_id: UUID,
    *,
    model_row_id: str,
    symbol: str,
    symbol_name: str,
    timeframe: str,
    num_days: int,
    ref_price: float | None,
    seed: int | None = None,
) -> SyntheticSeries:
    """生成完整合成 K 线序列。

    接入了历史数据（provider/CSV，见 synthetic_seed）时先随机抽取
    该品种一段真实历史作种子（微观结构校准：波动率聚集/跳空/量能
    分布）；未接入或失败静默回退纯合成路径，主链路不受影响。
    """
    tf = (timeframe or "1d").strip().lower()
    if tf not in BARS_PER_DAY:
        raise ValueError("不支持的 K 线周期")
    max_days = TIMEFRAME_MAX_DAYS.get(tf, 30)
    days = max(1, min(num_days, max_days))
    bars_per_day = BARS_PER_DAY[tf]
    num_bars = min(MAX_BARS, days * bars_per_day)

    # 随机历史种子：取段随机性与渲染共用同一 rng（seed 给定则整链可复现）
    profile: HistorySeedProfile | None = None
    if symbol:
        seed_rng = random.Random(seed) if seed is not None else random.Random()
        seg: list[dict[str, Any]] | None = None
        try:
            seg = await resolve_seed_segment(symbol, tf, num_bars, seed_rng)
        except Exception as exc:  # 种子失败绝不阻断生成
            logger.warning("历史种子获取异常，回退纯合成: %s", exc)
        if seg:
            profile = build_seed_profile(seg, tf, symbol=symbol)
        if profile is not None:
            logger.info(
                "合成历史种子就绪: %s %s %s~%s %d根",
                symbol, tf, profile.start, profile.end, len(profile.bars),
            )

    # 基准价/振幅：有种子时用种子末收与真实振幅中位（贴合该品种
    # 该时期的真实量级），无种子保持前端参考价/默认表
    rprice = profile.ref_price if profile else resolve_ref_price(symbol, ref_price)
    amp = profile.amplitude if profile else DEFAULT_AMPLITUDE.get(tf, 0.015)

    script = await generate_market_script(
        session, user_id, model_row_id,
        symbol=symbol, symbol_name=symbol_name, timeframe=tf,
        num_bars=num_bars, ref_price=rprice, amplitude=amp,
        seed_profile=profile,
    )
    bars = render_bars_from_script(
        script, symbol=symbol, timeframe=tf, num_bars=num_bars,
        ref_price=rprice, amplitude=amp, seed=seed,
        seed_profile=profile,
    )
    meta: dict[str, Any] = {
        "num_bars": len(bars),
        "num_days": days,
        "ref_price": rprice,
        "amplitude": amp,
        "symbol": symbol,
        "timeframe": tf,
    }
    if profile is not None:
        meta["history_seed"] = {
            "used": True,
            "symbol": profile.symbol,
            "start": profile.start,
            "end": profile.end,
            "bars": len(profile.bars),
            "bar_sigma": round(profile.bar_sigma, 6),
            "gap_rate": round(profile.gap_rate, 3),
            "vol_median": profile.vol_median,
        }
    else:
        meta["history_seed"] = {"used": False}
    return SyntheticSeries(bars=bars, script=script, meta=meta)


# ------------------------------------------------------------------
# step 推进器（边推边决策）
# ------------------------------------------------------------------

def account_to_snapshot(account: VirtualAccount, last_price: float) -> dict[str, Any]:
    """VirtualAccount → 可序列化快照"""
    return {
        "cash": round(account.cash, 2),
        "side": account.side,
        "qty": account.qty,
        "avg_price": round(account.avg_price, 4),
        "realized_pnl": round(account.realized_pnl, 2),
        "fees_paid": round(account.fees_paid, 2),
        "equity": round(account.equity(last_price), 2),
        "trades": account.trades[-200:],
        "last_price": round(last_price, 4),
    }


def snapshot_to_account(
    snap: dict[str, Any] | None, *, multiplier: int, margin_rate: float,
    fee_mode: str, open_fee: float, close_fee: float, initial_cash: float,
) -> VirtualAccount:
    """快照 → VirtualAccount（带既有 trades）"""
    if not snap:
        return VirtualAccount(
            cash=initial_cash, multiplier=multiplier, margin_rate=margin_rate,
            fee_mode=fee_mode, open_fee=open_fee, close_fee=close_fee,
        )
    return VirtualAccount(
        cash=float(snap.get("cash") or initial_cash),
        multiplier=multiplier, margin_rate=margin_rate,
        fee_mode=fee_mode, open_fee=open_fee, close_fee=close_fee,
        side=str(snap.get("side") or "flat"),
        qty=int(snap.get("qty") or 0),
        avg_price=float(snap.get("avg_price") or 0),
        realized_pnl=float(snap.get("realized_pnl") or 0),
        fees_paid=float(snap.get("fees_paid") or 0),
        trades=list(snap.get("trades") or []),
    )


async def step_decision(
    session: AsyncSession,
    user_id: UUID,
    *,
    bars_so_far: list[dict[str, Any]],
    account_snapshot: dict[str, Any] | None,
    strategy_type: str,
    strategy_params: dict[str, Any] | None,
    side_mode: str,
    fixed_qty: int,
    initial_cash: float,
    risk_style: str,
    custom_prompt_enabled: bool,
    custom_prompt: str | None,
    close_rules: dict[str, Any] | None,
    stop_rules: dict[str, Any] | None,
    max_hold_days: int,
    model_row_id: str | None,
    symbol: str,
    symbol_name: str,
    timeframe: str,
    decision_model_row_id: str | None = None,
    decision_min_interval_ms: int = 2000,
    last_decision_at: float | None = None,
) -> dict[str, Any]:
    """对最新一根 bar 做策略决策，推进账户，返回决策+快照+权益点。

    复用 apply_hard_rules / quant_signal / VirtualAccount，语义与 runner.py 一致。
    """
    if not bars_so_far:
        raise ValueError("bars_so_far 不能为空")

    i = len(bars_so_far) - 1
    bar = bars_so_far[i]
    price = float(bar.get("close") or 0)
    bar_time = str(bar.get("time") or "")
    bar_high = float(bar.get("high") or 0)
    bar_low = float(bar.get("low") or 0)
    if price <= 0:
        return {
            "action": "hold", "quantity": 0, "price": 0, "reason": "无效价格",
            "source": "skip", "executed": False,
            "decision_called_at": last_decision_at,
            "account_snapshot": account_snapshot, "equity_point": None, "decision": None,
        }

    # 合约参数（合成数据用品种真实合约乘数/保证金/手续费）
    params = resolve_trade_params(symbol or "DEFAULT", None)
    account = snapshot_to_account(
        account_snapshot,
        multiplier=int(params["multiplier"]),
        margin_rate=float(params["margin_rate"]),
        fee_mode=str(params["fee_mode"]),
        open_fee=float(params["open_fee"]),
        close_fee=float(params["close_fee"]),
        initial_cash=initial_cash,
    )

    pos = account.position_dict()
    # 持仓天数：从账户 trades 里找最近一次开仓时间，与当前 bar 时间差
    hold_days = _hold_days_between(account.trades, bar_time) if pos else None

    decision: dict[str, Any]
    executed = False
    exec_price = price
    # 决策模型限频：距上次调用不足间隔（回放速度 2-5s）的 bar 降级用策略信号。
    # decision_called_at 默认回传上次锚点：硬规则/限频降级/无效价 bar 不重置限频
    decision_called_at: float | None = last_decision_at

    # 硬规则（止损/止盈/最长持仓，盘中 high/low）
    hard = None
    if hard is None:
        hard = apply_hard_rules(
            pos, price,
            dict(stop_rules or {}),
            dict(close_rules or {}),
            fixed_qty,
            max_hold_days=max_hold_days,
            hold_days=hold_days,
            risk_style=(risk_style if strategy_type == "ai" else None),
            bar_high=bar_high, bar_low=bar_low,
        )
    if hard:
        act = str(hard.get("action") or "hold")
        qty = int(hard.get("quantity") or 0)
        reason = str(hard.get("reason") or "")
        exec_price = float(hard.get("trigger_price") or price)
        if exec_price <= 0:
            exec_price = price
        decision = {
            "time": bar_time, "action": act, "quantity": qty,
            "price": exec_price, "reason": reason, "source": "rule",
            "trigger_type": hard.get("trigger_type"),
        }
    else:
        # 2) 策略信号
        from app.services.backtest.data import slice_signal_window
        signal_bars = slice_signal_window(bars_so_far, i, 120)

        if strategy_type == "ai":
            sig = await _ai_signal_step(
                session, user_id, model_row_id,
                bars_so_far=signal_bars, bar=bar, pos=pos, price=price,
                symbol=symbol, symbol_name=symbol_name, timeframe=timeframe,
                side_mode=side_mode, fixed_qty=fixed_qty, risk_style=risk_style,
                custom_prompt_enabled=custom_prompt_enabled,
                custom_prompt=custom_prompt,
                max_hold_days=max_hold_days, hold_days=hold_days,
                close_rules=close_rules, stop_rules=stop_rules,
                initial_cash=initial_cash, account=account,
            )
            source = "ai"
        elif decision_model_row_id:
            # 决策模型混合模式：量化策略算信号喂给 Jev（System One），
            # 模型综合行情做最终投资判断（与实盘 decision 策略同构）
            quant = quant_signal(
                strategy_type, signal_bars,
                dict(strategy_params or {}),
                side_mode, pos,
            )
            # 调用限频：间隔=回放速度（2000-5000ms 钳制）。距上次调用不足
            # 间隔的 bar 不打 Jev，降级直接用策略信号（无状态：前端回传
            # 上次调用时间戳）；-100ms 容差避免回放节奏抖动导致误降级
            interval_ms = max(2000, min(5000, int(decision_min_interval_ms or 2000)))
            now_ms = datetime.now().timestamp() * 1000
            throttled = (
                last_decision_at is not None
                and (now_ms - float(last_decision_at)) < (interval_ms - 100)
            )
            stop_self = (stop_rules or {}).get("ai_auto")
            stop_self_off = stop_self is False
            if throttled:
                sig = dict(quant)
                sig["reason"] = (
                    f"{sig.get('reason') or ''}；决策模型限频中（间隔 {interval_ms / 1000:.1f}s），本根用策略信号"
                )[:200]
                source = str(strategy_type)
            else:
                sig = await _decision_signal_step(
                    session, user_id, decision_model_row_id,
                    bars_so_far=signal_bars, pos=pos, price=price,
                    symbol=symbol, symbol_name=symbol_name, timeframe=timeframe,
                    side_mode=side_mode, fixed_qty=fixed_qty,
                    hold_days=hold_days, max_hold_days=max_hold_days,
                    account=account,
                    strategy_kind=strategy_type,
                    quant_action=quant.get("action"),
                    quant_reason=quant.get("reason"),
                )
                decision_called_at = now_ms
                source = "decision"
            # 决策模型自主止损关闭时拦截平仓(含限频降级的策略平仓信号)
            if stop_self_off and str(sig.get("action") or "") == "close":
                sig = {
                    "action": "hold",
                    "quantity": 0,
                    "reason": "未开启决策模型自主止损，平仓被拦截",
                }
        else:
            sig = quant_signal(
                strategy_type, signal_bars,
                dict(strategy_params or {}),
                side_mode, pos,
            )
            source = strategy_type
        act, qty = normalize_action(
            str(sig.get("action") or "hold"),
            int(sig.get("quantity") or 0),
            fixed_qty, side_mode, account.position_dict(),
        )
        decision = {
            "time": bar_time, "action": act, "quantity": qty,
            "price": price, "reason": str(sig.get("reason") or "")[:200],
            "source": source,
        }

    # 执行开平（保持与 runner.py 一致）
    # 注意：account.apply 返回的 trade 含 pnl/fee/side，比 decision 更完整，
    # 前端成交明细需要 pnl 字段，因此优先用 trade 作为 decision 返回。
    executed_trade: dict[str, Any] | None = None
    if decision["action"] != "hold" and decision["quantity"] > 0:
        executed_trade = account.apply(
            decision["action"], decision["quantity"], exec_price, bar_time,
            decision["reason"],
        )
        if executed_trade:
            executed = True

    snap = account_to_snapshot(account, price)
    # 若执行了开平，用 account.apply 返回的完整 trade（含 pnl/fee/side）作为 decision，
    # 否则用原始 decision（hold/未执行）
    final_decision = executed_trade if executed_trade else decision
    return {
        "action": decision["action"],
        "quantity": decision["quantity"],
        "price": exec_price,
        "reason": decision["reason"],
        "source": decision["source"],
        "executed": executed,
        "decision": final_decision,
        # 决策模型本根是否真实调用及调用时刻（epoch ms）；限频降级/非决策模式为 null。
        # 前端持有并在下一根 step 回传 last_decision_at 实现无状态限频
        "decision_called_at": decision_called_at,
        "account_snapshot": snap,
        "equity_point": {
            "time": bar_time,
            "equity": snap["equity"],
            "cash": snap["cash"],
            "unrealized": round(account.unrealized(price), 2),
        },
    }


def _hold_days_between(trades: list[dict[str, Any]], bar_time: str) -> float | None:
    """从最近一次开仓 trade 时间到当前 bar 时间的持仓天数"""
    if not bar_time:
        return None
    open_time: str | None = None
    for t in reversed(trades):
        if str(t.get("action") or "").startswith("open_"):
            open_time = str(t.get("time") or "")
            break
    if not open_time:
        return None
    from app.services.backtest.runner import _hold_days
    return _hold_days(open_time, bar_time)


async def _ai_signal_step(
    session: AsyncSession,
    user_id: UUID,
    model_row_id: str | None,
    *,
    bars_so_far: list[dict[str, Any]],
    bar: dict[str, Any],
    pos: dict[str, Any] | None,
    price: float,
    symbol: str,
    symbol_name: str,
    timeframe: str,
    side_mode: str,
    fixed_qty: int,
    risk_style: str,
    custom_prompt_enabled: bool,
    custom_prompt: str | None,
    max_hold_days: int,
    hold_days: float | None,
    close_rules: dict[str, Any] | None,
    stop_rules: dict[str, Any] | None,
    initial_cash: float,
    account: VirtualAccount,
) -> dict[str, Any]:
    """复用 runner._ai_signal 的调用形态（构造伪 task）"""
    from app.services.backtest.runner import _ai_signal

    if not model_row_id:
        return {"action": "hold", "quantity": 0, "reason": "AI 未选模型"}
    context = {
        "symbol": symbol, "symbol_name": symbol_name, "timeframe": timeframe,
        "side_mode": side_mode, "position_mode": "fixed_qty",
        "fixed_qty": fixed_qty, "risk_style": risk_style,
        "style_active": not custom_prompt_enabled,
        "custom_prompt_enabled": custom_prompt_enabled,
        "max_hold_days": max_hold_days, "hold_days": hold_days,
        "allocated_capital": initial_cash, "ai_budget": account.cash,
        "last_price": price, "position": pos,
        "account": {"total_equity": account.equity(price), "available_margin": account.cash},
        "close_rules": {k: v for k, v in (close_rules or {}).items() if not str(k).startswith("_")},
        "stop_rules": {k: v for k, v in (stop_rules or {}).items() if not str(k).startswith("_")},
        "recent_bars": bars_so_far[-30:],
        "closed_bar": bar,
        "goal": "多赚少亏，以完成盈利目标为第一优先级；" + (
            "已启用用户提示词，策略风格失效" if custom_prompt_enabled else "风格在用户规则范围内调节"
        ),
    }
    try:
        return await _ai_signal(
            session, user_id, str(model_row_id), context,
            risk_style=risk_style, max_hold_days=max_hold_days,
            allocated_capital=initial_cash,
            custom_prompt_enabled=custom_prompt_enabled,
            custom_prompt=custom_prompt,
        )
    except Exception as exc:
        logger.warning("step AI 调用失败: %s", exc)
        return {"action": "hold", "quantity": 0, "reason": f"AI异常: {exc}"[:80]}


async def _decision_signal_step(
    session: AsyncSession,
    user_id: UUID,
    decision_model_row_id: str | None,
    *,
    bars_so_far: list[dict[str, Any]],
    pos: dict[str, Any] | None,
    price: float,
    symbol: str,
    symbol_name: str,
    timeframe: str,
    side_mode: str,
    fixed_qty: int,
    hold_days: float | None,
    max_hold_days: int,
    account: VirtualAccount,
    strategy_kind: str,
    quant_action: Any,
    quant_reason: Any,
) -> dict[str, Any]:
    """决策模型混合 step：策略信号喂给 Jev，模型做最终投资判断。

    复用实盘 decision_client.call_jev_decision（伪 task 只需 5 个属性），
    返回与 quant_signal 同构的 {action, quantity, confidence, reason}。
    """
    from types import SimpleNamespace

    from app.services.ai_trading.llm.decision_client import call_jev_decision

    if not decision_model_row_id:
        return {"action": "hold", "quantity": 0, "reason": "决策模型未选"}
    pseudo_task = SimpleNamespace(
        model_row_id=decision_model_row_id,
        user_id=user_id,
        symbol=symbol or "",
        symbol_name=symbol_name or "",
        timeframe=timeframe or "1d",
        side_mode=side_mode or "both",
    )
    # 回测 pos 只有方向/手数/均价：补浮盈亏与保证金,让模型能判断自主止损
    pos_for_state = dict(pos) if pos else None
    if pos_for_state and price > 0:
        pos_for_state["unrealized_pnl"] = round(account.unrealized(price), 2)
        pos_for_state["margin"] = round(
            float(pos_for_state.get("avg_price") or 0)
            * int(pos_for_state.get("quantity") or 0)
            * account.multiplier
            * account.margin_rate,
            2,
        )
    context = {
        "last_price": price,
        "position": pos_for_state,
        "recent_bars": bars_so_far[-40:],
        "ai_bars_limit": 40,
        "hold_days": hold_days,
        "max_hold_days": max_hold_days,
        "ai_budget": account.cash,
        "qty_min": fixed_qty,
        "qty_max": fixed_qty,
        "strategy_signal": (
            {
                "kind": strategy_kind,
                "action": str(quant_action or "hold"),
                "reason": str(quant_reason or "")[:160],
                "confidence": 1.0,
            }
            if quant_action
            else None
        ),
    }
    try:
        return await call_jev_decision(session, pseudo_task, context)
    except Exception as exc:
        logger.warning("step 决策模型调用失败: %s", exc)
        return {"action": "hold", "quantity": 0, "reason": f"决策模型异常: {exc}"[:80]}


def finish_replay(
    *,
    equity_curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    account_snapshot: dict[str, Any] | None,
    initial_cash: float,
    bars: list[dict[str, Any]],
    strategy_type: str,
    ai_calls: int,
    timeframe: str = "",
    symbol: str = "",
    symbol_name: str = "",
) -> dict[str, Any]:
    """回放结束：用与历史回测完全相同的 compute_metrics 算最终指标。

    组装成与 run_backtest 返回结构一致的报告，前端可直接复用 BacktestReportView。
    """
    from app.services.backtest.metrics import (
        compute_metrics,
        downsample_equity,
        summary_message,
    )
    from app.services.backtest.data import export_chart_bars
    from datetime import date as _date

    snap = account_snapshot or {}
    final_price = float(snap.get("last_price") or 0)
    final_eq = float(snap.get("equity") or initial_cash)
    realized_pnl = float(snap.get("realized_pnl") or 0)
    fees_paid = float(snap.get("fees_paid") or 0)

    metrics = compute_metrics(
        equity_curve, trades, initial_cash, final_eq, realized_pnl, fees_paid,
    )

    # 图表 K 线：合成数据无日期区间概念，直接用全部 bars（最多 1500 根）
    chart_bars = export_chart_bars(
        bars, _date(2000, 1, 1), _date(2100, 1, 1), 1500,
    )

    return {
        "status": "completed",
        "config": {
            "symbol": symbol,
            "symbol_name": symbol_name or "AI 合成 K 线",
            "timeframe": timeframe,
            "start_date": "",
            "end_date": "",
            "strategy_type": strategy_type,
            "strategy_params": None,
            "side_mode": "both",
            "fixed_qty": 0,
            "initial_cash": initial_cash,
            "risk_style": "balanced",
            "style_active": False,
            "max_hold_days": 0,
            "user_max_hold_days": 0,
            "custom_prompt_enabled": False,
            "model_row_id": None,
            "ai_calls": ai_calls,
            "bar_count": len(bars),
            "trade_bars": len(bars),
        },
        "metrics": metrics,
        "equity_curve": downsample_equity(equity_curve, 400),
        "bars": chart_bars,
        "trades": trades[-200:],
        "decisions": [],
        "message": summary_message(metrics, strategy_type, ai_calls)
        + "（AI 合成 K 线过拟合测试）",
    }
