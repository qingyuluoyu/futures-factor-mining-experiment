"""虚拟盘陈旧行情脉冲 —— openctp 停推时维持 7×24 活跃感

约束（重要）：
- 以 anchor_price（openctp 真 tick 锚价）为中心，只允许 ±1 最小跳
- 禁止在 last 上随机游走（曾导致 3076→3456，K 线与品种价脱节）
- 多数轮次只刷新 tick_time / volume，不改价
- 若已漂移（相对锚价或 live 价 >1.5%），先拉回再脉冲

不覆盖新浪补价；不写入 live 键。
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from app.services.market_keys import get_quote_json, write_keys_for_quote

logger = logging.getLogger("qihuo.virtual_quote_pulse")

_BJ_TZ = timezone(timedelta(hours=8))
_TTL = 7 * 24 * 3600
_STALE_SEC = 20.0
_MAX_PULSE_PER_ROUND = 50
# 相对锚价最大偏离：超过则强制回锚
_MAX_DRIFT_RATIO = 0.015
# 相对 live 价偏离过大也回拉（无锚时）
_MAX_LIVE_DRIFT = 0.02

# 进程内：symbol → (last_price, volume, mono_ts)
_last_seen: dict[str, tuple[float, int, float]] = {}


def _mono() -> float:
    import time

    return time.monotonic()


def _tick_size(quote: dict[str, Any], last: float) -> float:
    """最小跳动：优先 decimal_places，不用买卖价差（价差常=2 跳会放大漂移）"""
    try:
        dec = int(quote.get("decimal_places") or 0)
    except (TypeError, ValueError):
        dec = 0
    if dec > 0:
        return 10 ** (-dec)
    if last >= 10000:
        return 10.0
    if last >= 1000:
        return 1.0
    if last >= 100:
        return 0.1
    if last >= 10:
        return 0.01
    return max(last * 1e-4, 0.001)


def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    n = round(price / tick)
    return round(n * tick, 10)


def should_pulse(
    symbol: str,
    last_price: float,
    volume: int,
    now_mono: float,
    stale_sec: float,
) -> bool:
    prev = _last_seen.get(symbol)
    if prev is None:
        return True
    prev_last, prev_vol, prev_ts = prev
    if last_price != prev_last or volume != prev_vol:
        return False
    return (now_mono - prev_ts) >= stale_sec


def resolve_anchor(quote: dict[str, Any]) -> float:
    """锚价：优先 quote.anchor_price，否则非 pulse 的 last，再否则 last"""
    try:
        a = float(quote.get("anchor_price") or 0)
    except (TypeError, ValueError):
        a = 0.0
    if a > 0:
        return a
    try:
        last = float(quote.get("last_price") or 0)
    except (TypeError, ValueError):
        last = 0.0
    src = str(quote.get("source") or "")
    # 已是脉冲价且无锚：不可信，返回 0 让上层用 live 回拉
    if "pulse" in src:
        return 0.0
    return last if last > 0 else 0.0


def build_pulsed_quote(
    quote: dict[str, Any],
    now_bj: datetime,
    anchor: float,
) -> dict[str, Any] | None:
    """围绕锚价生成脉冲 quote：大多只刷新时间，价仅 ±1 跳"""
    if anchor <= 0:
        return None
    tick = _tick_size(quote, anchor)
    # 80% 只刷时间/量；20% 在锚价 ±1 跳
    roll = random.random()
    if roll < 0.80:
        new_last = _round_to_tick(anchor, tick)
    else:
        step = random.choice([-1, 1])
        new_last = _round_to_tick(anchor + step * tick, tick)
    if new_last <= 0:
        new_last = anchor

    out = dict(quote)
    out["anchor_price"] = anchor
    out["last_price"] = new_last
    out["bid_price"] = _round_to_tick(new_last - tick, tick)
    out["ask_price"] = _round_to_tick(new_last + tick, tick)
    if float(out.get("bid_price") or 0) <= 0:
        out["bid_price"] = new_last
    try:
        vol = int(float(quote.get("volume") or 0))
    except (TypeError, ValueError):
        vol = 0
    # 成交感：小幅加量，不依赖价变
    if random.random() < 0.55:
        out["volume"] = vol + random.randint(1, 2)
    pre = float(quote.get("pre_close") or anchor)
    if pre > 0:
        out["change"] = new_last - pre
        out["change_pct"] = (out["change"] / pre) * 100.0
    out["tick_time"] = now_bj.strftime("%H%M%S")
    out["recv_ts"] = now_bj.timestamp()
    src = str(quote.get("source") or "ctp")
    # 去掉重复 +pulse
    base = src.replace("+pulse", "").strip() or "ctp"
    out["source"] = f"{base}+pulse"
    return out


def note_seen(symbol: str, last_price: float, volume: int, now_mono: float) -> None:
    _last_seen[symbol] = (last_price, volume, now_mono)


def _drift_ratio(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return abs(a - b) / a


async def _live_last(redis_conn: aioredis.Redis, symbol: str) -> float:
    """读 live 最新价作回拉参考（周末可能是新浪收盘价）"""
    try:
        q = await get_quote_json(redis_conn, "live", symbol)
    except Exception:
        return 0.0
    if not q:
        return 0.0
    try:
        return float(q.get("last_price") or 0)
    except (TypeError, ValueError):
        return 0.0


async def repair_drifted_virtual_quotes(redis_conn: aioredis.Redis) -> int:
    """把脉冲游走过远的 virtual 价拉回锚价 / live，并清脏 forming K

    返回修复条数。
    """
    from app.services.market_channel import collect_quotes_from_redis
    from app.services.kline_keys import realtime_kline_scan_pattern

    quotes = await collect_quotes_from_redis(redis_conn, "virtual")
    repaired = 0
    now_bj = datetime.now(_BJ_TZ)
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().lower()
        if not symbol:
            continue
        try:
            last = float(quote.get("last_price") or 0)
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue
        src = str(quote.get("source") or "")
        anchor = resolve_anchor(quote)
        live = await _live_last(redis_conn, symbol)
        need = False
        target = 0.0
        if anchor > 0 and _drift_ratio(anchor, last) > _MAX_DRIFT_RATIO:
            need = True
            target = anchor
        elif "pulse" in src and live > 0 and _drift_ratio(live, last) > _MAX_LIVE_DRIFT:
            need = True
            target = live
        elif "pulse" in src and anchor <= 0 and live > 0:
            # 无锚的脉冲价：统一回 live
            if _drift_ratio(live, last) > 0.005:
                need = True
                target = live
        if not need:
            # 确保有 anchor 字段
            if anchor <= 0 and last > 0 and "pulse" not in src:
                q2 = dict(quote)
                q2["anchor_price"] = last
                try:
                    for key in write_keys_for_quote("virtual", symbol):
                        await redis_conn.set(
                            key, json.dumps(q2, ensure_ascii=False), ex=_TTL
                        )
                except Exception:
                    pass
            continue

        tick = _tick_size(quote, target)
        fixed = dict(quote)
        fixed["last_price"] = _round_to_tick(target, tick)
        fixed["anchor_price"] = target
        fixed["bid_price"] = _round_to_tick(target - tick, tick)
        fixed["ask_price"] = _round_to_tick(target + tick, tick)
        pre = float(quote.get("pre_close") or target)
        if pre > 0:
            fixed["change"] = fixed["last_price"] - pre
            fixed["change_pct"] = (fixed["change"] / pre) * 100.0
        fixed["tick_time"] = now_bj.strftime("%H%M%S")
        fixed["recv_ts"] = now_bj.timestamp()
        # 修复后先标 ctp 锚，下一轮再 pulse
        fixed["source"] = "ctp:openctp_724"
        try:
            for key in write_keys_for_quote("virtual", symbol):
                await redis_conn.set(
                    key, json.dumps(fixed, ensure_ascii=False), ex=_TTL
                )
            repaired += 1
        except Exception:
            logger.exception("修复 virtual 漂移价失败 %s", symbol)

    # 清 virtual forming，避免脏 OHLC 继续画
    if repaired:
        try:
            pattern = realtime_kline_scan_pattern("virtual")
            async for key in redis_conn.scan_iter(match=pattern, count=200):
                await redis_conn.delete(key)
        except Exception:
            logger.exception("清理 virtual forming K 失败")
        # 进程快照作废，让脉冲重新记锚
        _last_seen.clear()
        logger.info("virtual 漂移价已修复 %d 条，forming K 已清空", repaired)
    return repaired


async def trim_pulse_hist_bars(redis_conn: aioredis.Redis) -> int:
    """历史库里 source=virtual 且相对 seed 末根偏离过大的 bar 剔除"""
    from app.data.contracts import ALL_CONTRACTS
    from app.services.kline_keys import history_kline_key
    from app.services.kline_virtual_hist import save_virtual_history, load_virtual_history

    periods = ["1m", "5m", "15m", "30m", "60m", "1d"]
    n_trim = 0
    for c in ALL_CONTRACTS:
        sym = str(c.get("symbol") or "").lower()
        if not sym:
            continue
        for period in periods:
            bars = await load_virtual_history(redis_conn, sym, period)
            if not bars:
                continue
            # 找最后一根 seed 的 close 作参考
            seed_close = 0.0
            for b in reversed(bars):
                if str(b.get("source") or "") == "seed":
                    try:
                        seed_close = float(b.get("close") or 0)
                    except (TypeError, ValueError):
                        seed_close = 0.0
                    break
            if seed_close <= 0:
                continue
            kept: list[dict[str, Any]] = []
            changed = False
            for b in bars:
                src = str(b.get("source") or "")
                if src != "virtual":
                    kept.append(b)
                    continue
                try:
                    close = float(b.get("close") or 0)
                except (TypeError, ValueError):
                    close = 0.0
                if close > 0 and _drift_ratio(seed_close, close) > 0.03:
                    changed = True
                    n_trim += 1
                    continue
                kept.append(b)
            if changed:
                await save_virtual_history(redis_conn, sym, period, kept)
    if n_trim:
        logger.info("virtual 历史脏 bar 已剔除 %d 根", n_trim)
    return n_trim


async def pulse_stale_virtual_quotes(
    redis_conn: aioredis.Redis,
    stale_sec: float = _STALE_SEC,
    max_per_round: int = _MAX_PULSE_PER_ROUND,
) -> int:
    """扫描 virtual quote，对陈旧 ctp 源做锚价脉冲。返回脉冲条数。"""
    from app.services.market_channel import collect_quotes_from_redis

    quotes = await collect_quotes_from_redis(redis_conn, "virtual")
    if not quotes:
        return 0

    now_m = _mono()
    now_bj = datetime.now(_BJ_TZ)
    pulsed = 0
    random.shuffle(quotes)

    for quote in quotes:
        if pulsed >= max_per_round:
            break
        symbol = str(quote.get("symbol") or "").strip().lower()
        if not symbol:
            continue
        src = str(quote.get("source") or "")
        if src.startswith("sina"):
            continue
        if src and not (
            src.startswith("ctp") or "openctp" in src or "pulse" in src
        ):
            continue
        try:
            last = float(quote.get("last_price") or 0)
            vol = int(float(quote.get("volume") or 0))
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue

        prev = _last_seen.get(symbol)
        if prev is None:
            note_seen(symbol, last, vol, now_m)
            continue
        prev_last, prev_vol, prev_ts = prev
        # 价/量变化：若是真 tick（无 pulse）刷新；脉冲内自变也刷新时间
        if last != prev_last or vol != prev_vol:
            note_seen(symbol, last, vol, now_m)
            continue
        if (now_m - prev_ts) < stale_sec:
            continue

        anchor = resolve_anchor(quote)
        if anchor <= 0:
            live = await _live_last(redis_conn, symbol)
            anchor = live if live > 0 else last
        # 漂移过大先不 pulse（等 repair 轮）
        if _drift_ratio(anchor, last) > _MAX_DRIFT_RATIO:
            continue

        pulsed_q = build_pulsed_quote(quote, now_bj, anchor)
        if pulsed_q is None:
            continue
        payload = json.dumps(pulsed_q, ensure_ascii=False)
        try:
            for key in write_keys_for_quote("virtual", symbol):
                await redis_conn.set(key, payload, ex=_TTL)
        except Exception:
            logger.exception("virtual 脉冲写价失败 %s", symbol)
            continue
        try:
            new_last = float(pulsed_q.get("last_price") or last)
            new_vol = int(float(pulsed_q.get("volume") or vol))
        except (TypeError, ValueError):
            new_last, new_vol = last, vol
        note_seen(symbol, new_last, new_vol, now_m)
        pulsed += 1

    if pulsed:
        logger.debug("virtual 锚价脉冲 %d 条", pulsed)
    return pulsed
