"""行情筛选引擎 —— 全市场主力 · 多指标条件组合 · 点击触发

筛选由用户点击触发，支持 MACD / 均线 / KDJ / RSI / BOLL
五类条件的参数调整与 AND 组合，在最近 window 根已收盘K内匹配。

指标算法复用 app.services.ai.tools.calc（与前端 indicators.ts 对齐）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.trading_mode import MODE_VIRTUAL, normalize_trading_mode
from app.services.ai.tools.calc import (
    calc_boll,
    calc_kdj,
    calc_macd,
    calc_rsi,
    calc_sma,
)
from app.services.kline import get_kline_history
from app.services.signal_detect import period_label, resolve_signal_bar_index
from app.services.signal_session import filter_session_bars
from app.services.signal_universe import list_all_master_symbols

logger = logging.getLogger("qihuo.market_screener")

_SUPPORTED = ("1d", "60m", "30m", "15m", "5m")
# 与前端图表 K 线加载量对齐（use-kline-history.ts PAGE_LIMIT=200）
_BASE_LOOKBACK: dict[str, int] = {
    "1d": 200,
    "60m": 200,
    "30m": 200,
    "15m": 200,
    "5m": 200,
}
_SCAN_CONCURRENCY = 10
_MAX_LOOKBACK = 500

# 各条件类型的 op → 中文标签 / 方向
_OP_META: dict[str, dict[str, str]] = {
    "macd": {
        "golden": "MACD金叉",
        "death": "MACD死叉",
        "dif_above": "DIF在DEA上方",
        "dif_below": "DIF在DEA下方",
    },
    "ma": {
        "bull_arrange": "均线多头排列",
        "bear_arrange": "均线空头排列",
        "cross_above": "上穿均线",
        "cross_below": "下穿均线",
    },
    "kdj": {
        "golden": "KDJ金叉",
        "death": "KDJ死叉",
        "overbought": "KDJ超买",
        "oversold": "KDJ超卖",
    },
    "rsi": {
        "overbought": "RSI超买",
        "oversold": "RSI超卖",
        "cross_up_50": "RSI上穿50",
        "cross_down_50": "RSI下穿50",
    },
    "boll": {
        "break_upper": "突破布林上轨",
        "break_lower": "跌破布林下轨",
        "above_middle": "布林中轨上方",
        "below_middle": "布林中轨下方",
    },
}

_OP_DIRECTION: dict[str, str] = {
    "golden": "long",
    "death": "short",
    "dif_above": "long",
    "dif_below": "short",
    "bull_arrange": "long",
    "bear_arrange": "short",
    "cross_above": "long",
    "cross_below": "short",
    "break_upper": "long",
    "break_lower": "short",
    "above_middle": "long",
    "below_middle": "short",
    "cross_up_50": "long",
    "cross_down_50": "short",
    "long": "long",
    "short": "short",
}


def condition_label(cond: dict[str, Any]) -> str:
    """条件的展示标签。"""
    ctype = str(cond.get("type") or "")
    op = str(cond.get("op") or "")
    return _OP_META.get(ctype, {}).get(op, op or ctype)


def _condition_directions(conds: list[dict[str, Any]]) -> str:
    """聚合各条件方向：全多→long，全空→short，混杂→mixed，无方向→neutral。"""
    dirs: list[str] = []
    for cond in conds:
        key = str(cond.get("op") or "")
        d = _OP_DIRECTION.get(key)
        if d and d not in dirs:
            dirs.append(d)
    if not dirs:
        return "neutral"
    if len(dirs) == 1:
        return dirs[0]
    return "mixed"


def normalize_period(raw: str | None) -> str:
    value = (raw or "1d").strip().lower()
    return value if value in _SUPPORTED else "1d"


def _valid_ohlc_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for bar in bars:
        try:
            if (
                bar.get("high") is not None
                and bar.get("low") is not None
                and bar.get("close") is not None
            ):
                out.append(bar)
        except (TypeError, ValueError):
            continue
    return out


def _series(bars: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    closes = [float(b["close"]) for b in bars]
    return highs, lows, closes


def _window_start(sig_i: int, window: int) -> int:
    """交叉/事件检索窗口起点（含）。"""
    return max(1, sig_i - max(1, window) + 1)


def _eval_macd(
    closes: list[float], sig_i: int, cond: dict[str, Any]
) -> dict[str, Any]:
    op = str(cond.get("op") or "golden")
    fast = int(cond.get("fast") or 12)
    slow = int(cond.get("slow") or 26)
    signal_p = int(cond.get("signal") or 9)
    # 最小数据量按慢线参数放大
    need = max(30, slow)
    if len(closes) < need or sig_i < 1:
        return {"passed": False, "detail": f"数据不足（需约{need}根K）"}

    points = calc_macd(closes, fast, slow, signal_p)

    def dif_dea(i: int) -> tuple[float | None, float | None]:
        p = points[i]
        return p.get("dif"), p.get("dea")

    if op in ("dif_above", "dif_below"):
        f1, e1 = dif_dea(sig_i)
        if f1 is None or e1 is None:
            return {"passed": False, "detail": "指标未形成"}
        if op == "dif_above":
            ok = f1 > e1
            return {
                "passed": ok,
                "detail": f"DIF {f1:.4f} {'>' if ok else '≤'} DEA {e1:.4f}",
            }
        ok = f1 < e1
        return {
            "passed": ok,
            "detail": f"DIF {f1:.4f} {'<' if ok else '≥'} DEA {e1:.4f}",
        }

    window = int(cond.get("window") or 1)
    for i in range(_window_start(sig_i, window), sig_i + 1):
        f0, e0 = dif_dea(i - 1)
        f1, e1 = dif_dea(i)
        if None in (f0, e0, f1, e1):
            continue
        if op == "golden" and f0 <= e0 and f1 > e1:
            return {
                "passed": True,
                "detail": f"第{i + 1}根K线 DIF上穿DEA（{f1:.4f}>{e1:.4f}）",
            }
        if op == "death" and f0 >= e0 and f1 < e1:
            return {
                "passed": True,
                "detail": f"第{i + 1}根K线 DIF下穿DEA（{f1:.4f}<{e1:.4f}）",
            }
    return {"passed": False, "detail": f"最近{window}根收盘K无{condition_label(cond)}"}


def _eval_ma(closes: list[float], sig_i: int, cond: dict[str, Any]) -> dict[str, Any]:
    op = str(cond.get("op") or "bull_arrange")
    p_short = int(cond.get("ma_short") or 5)
    p_mid = int(cond.get("ma_mid") or 10)
    p_long = int(cond.get("ma_long") or 20)

    if op in ("bull_arrange", "bear_arrange"):
        need = max(p_short, p_mid, p_long) + 2
        if len(closes) < need:
            return {"passed": False, "detail": f"数据不足（需约{need}根K）"}
        ma_s = calc_sma(closes, p_short)
        ma_m = calc_sma(closes, p_mid)
        ma_l = calc_sma(closes, p_long)
        c = closes[sig_i]
        s, m, l = ma_s[sig_i], ma_m[sig_i], ma_l[sig_i]
        if None in (s, m, l):
            return {"passed": False, "detail": "指标未形成"}
        fs, fm, fl = float(s), float(m), float(l)
        if op == "bull_arrange":
            ok = c > fs > fm > fl
            return {
                "passed": ok,
                "detail": (
                    f"收盘{c:.2f} {'>' if ok else '≤'} MA{p_short}({fs:.2f}) "
                    f"> MA{p_mid}({fm:.2f}) > MA{p_long}({fl:.2f})"
                    if ok
                    else f"当前排列不满足 收盘{c:.2f}/MA{p_short}({fs:.2f})/MA{p_mid}({fm:.2f})/MA{p_long}({fl:.2f})"
                ),
            }
        ok = c < fs < fm < fl
        return {
            "passed": ok,
            "detail": (
                f"收盘{c:.2f} {'<' if ok else '≥'} MA{p_short}({fs:.2f}) "
                f"< MA{p_mid}({fm:.2f}) < MA{p_long}({fl:.2f})"
                if ok
                else f"当前排列不满足 收盘{c:.2f}/MA{p_short}({fs:.2f})/MA{p_mid}({fm:.2f})/MA{p_long}({fl:.2f})"
            ),
        }

    p = int(cond.get("cross_period") or p_long)
    need = p + 2
    if len(closes) < need or sig_i < 1:
        return {"passed": False, "detail": f"数据不足（需约{need}根K）"}
    ma = calc_sma(closes, p)
    window = int(cond.get("window") or 1)
    for i in range(_window_start(sig_i, window), sig_i + 1):
        cur, prev = ma[i], ma[i - 1]
        if cur is None or prev is None:
            continue
        c1, c0 = closes[i], closes[i - 1]
        if op == "cross_above" and c0 <= prev and c1 > cur:
            return {
                "passed": True,
                "detail": f"第{i + 1}根K线收盘上穿MA{p}（{c1:.2f}>{cur:.2f}）",
            }
        if op == "cross_below" and c0 >= prev and c1 < cur:
            return {
                "passed": True,
                "detail": f"第{i + 1}根K线收盘下穿MA{p}（{c1:.2f}<{cur:.2f}）",
            }
    cur = ma[sig_i]
    tail = (
        f"收盘{closes[sig_i]:.2f} vs MA{p}({cur:.2f})"
        if cur is not None
        else "指标未形成"
    )
    return {"passed": False, "detail": f"最近{window}根收盘K无{condition_label(cond)} · {tail}"}


def _eval_kdj(
    highs: list[float], lows: list[float], closes: list[float], sig_i: int, cond: dict[str, Any]
) -> dict[str, Any]:
    op = str(cond.get("op") or "golden")
    n = int(cond.get("n") or 9)
    k_p = int(cond.get("k_smooth") or 3)
    d_p = int(cond.get("d_smooth") or 3)
    ob = float(cond.get("overbought") if cond.get("overbought") is not None else 80)
    os_ = float(cond.get("oversold") if cond.get("oversold") is not None else 20)
    if len(closes) < n + k_p + d_p + 5 or sig_i < 1:
        return {"passed": False, "detail": "数据不足"}

    points = calc_kdj(highs, lows, closes, n, k_p, d_p)

    if op == "overbought":
        k1 = points[sig_i].get("k")
        if k1 is None:
            return {"passed": False, "detail": "指标未形成"}
        ok = float(k1) >= ob
        return {"passed": ok, "detail": f"K={k1:.2f} {'≥' if ok else '<'} 超买线{ob:.0f}"}
    if op == "oversold":
        k1 = points[sig_i].get("k")
        if k1 is None:
            return {"passed": False, "detail": "指标未形成"}
        ok = float(k1) <= os_
        return {"passed": ok, "detail": f"K={k1:.2f} {'≤' if ok else '>'} 超卖线{os_:.0f}"}

    window = int(cond.get("window") or 1)
    for i in range(_window_start(sig_i, window), sig_i + 1):
        prev, cur = points[i - 1], points[i]
        k0, d0 = prev.get("k"), prev.get("d")
        k1, d1 = cur.get("k"), cur.get("d")
        if None in (k0, d0, k1, d1):
            continue
        if op == "golden" and k0 <= d0 and k1 > d1:
            return {
                "passed": True,
                "detail": f"第{i + 1}根K线 K上穿D（K={k1:.2f} > D={d1:.2f}）",
            }
        if op == "death" and k0 >= d0 and k1 < d1:
            return {
                "passed": True,
                "detail": f"第{i + 1}根K线 K下穿D（K={k1:.2f} < D={d1:.2f}）",
            }
    return {"passed": False, "detail": f"最近{window}根收盘K无{condition_label(cond)}"}


def _eval_rsi(closes: list[float], sig_i: int, cond: dict[str, Any]) -> dict[str, Any]:
    op = str(cond.get("op") or "overbought")
    period = int(cond.get("period") or 14)
    ob = float(cond.get("overbought") if cond.get("overbought") is not None else 70)
    os_ = float(cond.get("oversold") if cond.get("oversold") is not None else 30)
    if len(closes) < period + 5 or sig_i < 1:
        return {"passed": False, "detail": "数据不足"}

    rsi = calc_rsi(closes, period)

    if op == "overbought":
        v = rsi[sig_i]
        if v is None:
            return {"passed": False, "detail": "指标未形成"}
        ok = v >= ob
        return {"passed": ok, "detail": f"RSI={v:.2f} {'≥' if ok else '<'} 超买线{ob:.0f}"}
    if op == "oversold":
        v = rsi[sig_i]
        if v is None:
            return {"passed": False, "detail": "指标未形成"}
        ok = v <= os_
        return {"passed": ok, "detail": f"RSI={v:.2f} {'≤' if ok else '>'} 超卖线{os_:.0f}"}

    window = int(cond.get("window") or 1)
    for i in range(_window_start(sig_i, window), sig_i + 1):
        v0, v1 = rsi[i - 1], rsi[i]
        if v0 is None or v1 is None:
            continue
        if op == "cross_up_50" and v0 <= 50 < v1:
            return {"passed": True, "detail": f"第{i + 1}根K线 RSI上穿50（{v1:.2f}）"}
        if op == "cross_down_50" and v0 >= 50 > v1:
            return {"passed": True, "detail": f"第{i + 1}根K线 RSI下穿50（{v1:.2f}）"}
    return {"passed": False, "detail": f"最近{window}根收盘K无{condition_label(cond)}"}


def _eval_boll(closes: list[float], sig_i: int, cond: dict[str, Any]) -> dict[str, Any]:
    op = str(cond.get("op") or "break_upper")
    period = int(cond.get("period") or 20)
    std_mult = float(cond.get("std") or 2.0)
    if len(closes) < period + 2 or sig_i < 1:
        return {"passed": False, "detail": "数据不足"}

    points = calc_boll(closes, period, std_mult)

    if op in ("above_middle", "below_middle"):
        mid = points[sig_i].get("middle")
        if mid is None:
            return {"passed": False, "detail": "指标未形成"}
        c = closes[sig_i]
        if op == "above_middle":
            ok = c > mid
            return {"passed": ok, "detail": f"收盘{c:.2f} {'>' if ok else '≤'} 中轨{mid:.2f}"}
        ok = c < mid
        return {"passed": ok, "detail": f"收盘{c:.2f} {'<' if ok else '≥'} 中轨{mid:.2f}"}

    window = int(cond.get("window") or 1)
    for i in range(_window_start(sig_i, window), sig_i + 1):
        band = points[i]
        u, l = band.get("upper"), band.get("lower")
        if u is None or l is None:
            continue
        c = closes[i]
        if op == "break_upper" and c > u:
            return {"passed": True, "detail": f"第{i + 1}根K线收盘{c:.2f} > 上轨{u:.2f}"}
        if op == "break_lower" and c < l:
            return {"passed": True, "detail": f"第{i + 1}根K线收盘{c:.2f} < 下轨{l:.2f}"}
    return {"passed": False, "detail": f"最近{window}根收盘K无{condition_label(cond)}"}


def evaluate_conditions(
    bars: list[dict[str, Any]],
    sig_i: int,
    conds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """逐条件评估，返回 [{type,label,passed,detail}]；bars 为已过滤的收盘序列。"""
    highs, lows, closes = _series(bars)
    out: list[dict[str, Any]] = []
    for cond in conds:
        ctype = str(cond.get("type") or "")
        label = condition_label(cond)
        if ctype == "macd":
            res = _eval_macd(closes, sig_i, cond)
        elif ctype == "ma":
            res = _eval_ma(closes, sig_i, cond)
        elif ctype == "kdj":
            res = _eval_kdj(highs, lows, closes, sig_i, cond)
        elif ctype == "rsi":
            res = _eval_rsi(closes, sig_i, cond)
        elif ctype == "boll":
            res = _eval_boll(closes, sig_i, cond)
        else:
            res = {"passed": False, "detail": f"未知条件类型 {ctype}"}
        out.append(
            {
                "type": ctype,
                "label": label,
                "passed": bool(res.get("passed")),
                "detail": str(res.get("detail") or ""),
            }
        )
    return out


def condition_lookback(conds: list[dict[str, Any]], period: str) -> int:
    """按条件参数推导所需K线数量。"""
    need = _BASE_LOOKBACK.get(period, 120)
    for cond in conds:
        ctype = str(cond.get("type") or "")
        window = int(cond.get("window") or 1)
        c_need = 0
        if ctype == "macd":
            c_need = int(cond.get("slow") or 26) + int(cond.get("signal") or 9) + 10
        elif ctype == "ma":
            c_need = max(
                int(cond.get("ma_long") or 20),
                int(cond.get("cross_period") or 20),
            ) * 2
        elif ctype == "kdj":
            c_need = (int(cond.get("n") or 9) + 10) * 2
        elif ctype == "rsi":
            c_need = int(cond.get("period") or 14) * 2
        elif ctype == "boll":
            c_need = int(cond.get("period") or 20) * 2
        need = max(need, c_need + window)
    return min(need, _MAX_LOOKBACK)


async def scan_symbol_screener(
    redis_conn: Any,
    symbol: str,
    name: str,
    period: str,
    conds: list[dict[str, Any]],
    lookback: int = 0,
    product_code: str = "",
    contract_name: str = "",
    trading_mode: str = "live",
) -> dict[str, Any] | None:
    """扫描单主力：所有条件全部满足才命中，否则返回 None。"""
    if not conds:
        return None
    mode = normalize_trading_mode(trading_mode)
    lb = lookback or condition_lookback(conds, period)
    try:
        bars = await get_kline_history(
            redis_conn,
            symbol,
            period,
            limit=lb,
            end_time="",
            session=None,
            trading_mode=mode,
        )
    except Exception:
        logger.debug("拉取K线失败 %s %s", symbol, period, exc_info=True)
        return None

    bars = _valid_ohlc_bars(
        filter_session_bars(bars or [], period, trading_mode=mode)
    )
    if len(bars) < 15:
        return None

    sig_i = resolve_signal_bar_index(bars, period)
    if sig_i is None or sig_i < 3:
        return None

    results = evaluate_conditions(bars, sig_i, conds)
    if not all(r["passed"] for r in results):
        return None

    code = product_code or "".join(ch for ch in symbol if ch.isalpha()).upper()
    zh_name = name or code
    zh_contract = contract_name or (
        f"{zh_name}{''.join(ch for ch in symbol if ch.isdigit())}"
    )
    signal_bar = bars[sig_i]
    return {
        "symbol": symbol,
        "product_code": code,
        "name": zh_name,
        "product_name": zh_name,
        "contract_name": zh_contract,
        "period": period,
        "as_of": str(signal_bar.get("time") or ""),
        "last_price": float(signal_bar.get("close") or 0) or None,
        "direction": _condition_directions(conds),
        "conditions": results,
    }


async def run_screener(
    redis_conn: Any,
    period: str,
    conds: list[dict[str, Any]],
    trading_mode: str = "live",
) -> dict[str, Any]:
    """全市场主力筛选：点击触发，同步扫描返回。"""
    started = time.monotonic()
    period = normalize_period(period)
    mode = normalize_trading_mode(trading_mode)
    pl = period_label(period)
    payload: dict[str, Any] = {
        "period": period,
        "period_label": pl,
        "trading_mode": mode,
        "scanned": 0,
        "total": 0,
        "items": [],
        "message": "",
        "meta": {"condition_count": len(conds)},
    }
    if not conds:
        payload["message"] = "请至少添加一个筛选条件"
        return payload

    symbols = await list_all_master_symbols(redis_conn)
    if not symbols:
        payload["message"] = "主力合约列表为空，请稍后重试"
        return payload

    lookback = condition_lookback(conds, period)
    sem = asyncio.Semaphore(_SCAN_CONCURRENCY)

    async def _one(item: dict[str, str]) -> dict[str, Any] | None:
        async with sem:
            return await scan_symbol_screener(
                redis_conn,
                item["symbol"],
                item.get("product_name") or item.get("name") or item["symbol"],
                period,
                conds,
                lookback=lookback,
                product_code=item.get("product_code") or "",
                contract_name=item.get("contract_name") or "",
                trading_mode=trading_mode,
            )

    gathered = await asyncio.gather(
        *[_one(item) for item in symbols],
        return_exceptions=True,
    )
    items: list[dict[str, Any]] = []
    for part in gathered:
        if isinstance(part, Exception):
            continue
        if part is not None:
            items.append(part)

    # 多头在前、空头居中、中性/混合在后；同向内按品种代码排序
    order = {"long": 0, "short": 1, "mixed": 2, "neutral": 3}
    items.sort(key=lambda e: (order.get(str(e.get("direction")), 9), str(e.get("product_code", ""))))

    elapsed_ms = int((time.monotonic() - started) * 1000)
    payload["scanned"] = len(symbols)
    payload["items"] = items
    payload["total"] = len(items)
    payload["elapsed_ms"] = elapsed_ms
    mode_zh = "虚拟盘" if mode == MODE_VIRTUAL else "实盘"
    payload["message"] = (
        f"{mode_zh} · {pl} · 扫描主力 {len(symbols)} · 命中 {len(items)} · "
        f"用时 {elapsed_ms / 1000:.1f}s"
    )
    return payload
