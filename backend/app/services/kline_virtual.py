"""虚拟盘实时 K 线 —— openctp quote → forming + 收盘归档

- forming：kline:virtual:realtime:*
- 跨周期：旧 bar 写入 kline:virtual:history:*（见 kline_virtual_hist）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from app.services.kline_keys import realtime_kline_key

logger = logging.getLogger("qihuo.kline_virtual")

_BJ_TZ = timezone(timedelta(hours=8))
_TTL_REALTIME = 300
_PERIODS: list[str] = ["1m", "5m", "15m", "30m", "60m", "1d"]
_PERIOD_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
}

_state: dict[str, dict[str, dict[str, Any]]] = {}


def _period_bucket(period: str, now: datetime) -> datetime:
    """虚拟盘 7×24：按北京墙钟对齐 bar 时间（周期起点）

    与实盘新浪「周期结束时刻 / 期货交易日」不同：
    - 14:30 看 60m → 14:00（当前小时桶），不是 15:00
    - 14:30 看 1m → 14:30，不是 14:31
    - 日线 → 当天自然日，不是夜盘归属的下一交易日
    """
    if period == "1d":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    minutes = _PERIOD_MINUTES.get(period, 1)
    total = now.hour * 60 + now.minute
    start_total = (total // minutes) * minutes
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=start_total)


def _bar_time_key(period: str, time_str: str) -> str:
    t = str(time_str or "").strip()
    if period == "1d":
        return t[:10]
    return t


def _price_sane(ref: float, price: float, max_dev: float = 0.015) -> bool:
    """相对参考价偏离是否在合理范围（默认 1.5%）

    用于过滤串月/脏 tick，避免一根 1m K 线被拉成 60+ 点假波动。
    """
    if ref <= 0 or price <= 0:
        return False
    return abs(price - ref) / ref <= max_dev


async def update_virtual_realtime_klines(
    redis_conn: aioredis.Redis,
    quotes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """用 virtual quote 更新 forming；跨周期时归档收盘 bar"""
    if not quotes:
        return []
    from app.services.kline_virtual_hist import append_closed_bar

    now = datetime.now(_BJ_TZ)
    updated: list[dict[str, Any]] = []

    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().lower()
        if not symbol:
            continue
        # 仅使用 openctp / 脉冲续价做 K 线；新浪补价不参与 forming
        src = str(quote.get("source") or "")
        if src.startswith("sina"):
            continue
        # 允许 ctp:*、*+pulse、virtual_pulse
        if not (
            src.startswith("ctp")
            or "openctp" in src
            or "pulse" in src
        ):
            continue
        try:
            last_price = float(quote.get("last_price") or 0)
        except (TypeError, ValueError):
            continue
        if last_price <= 0:
            continue
        try:
            total_volume = int(float(quote.get("volume") or 0))
        except (TypeError, ValueError):
            total_volume = 0
        position = quote.get("position")

        if symbol not in _state:
            _state[symbol] = {}

        for period in _PERIODS:
            is_daily = period == "1d"
            bucket = _period_bucket(period, now)
            if is_daily:
                bar_time = bucket.strftime("%Y-%m-%d")
            else:
                bar_time = bucket.strftime("%Y-%m-%d %H:%M:%S")

            st = _state[symbol].get(period)
            state_key = _bar_time_key(
                period, str(st.get("bar_time") or "") if st else ""
            )
            new_key = _bar_time_key(period, bar_time)

            # 跨周期：先归档旧 bar
            if st is not None and state_key and state_key != new_key:
                try:
                    await append_closed_bar(
                        redis_conn, symbol, period, dict(st.get("bar") or {})
                    )
                except Exception:
                    logger.exception(
                        "归档 virtual 收盘 K 失败 %s %s", symbol, period
                    )

            if st is None or state_key != new_key:
                st = _new_state(
                    is_daily,
                    bar_time,
                    last_price,
                    total_volume,
                    position,
                    quote,
                )
                _state[symbol][period] = st
            else:
                # 同 bar：串月脏 tick 仍拒；锚价脉冲允许小幅更新 close
                bar_open = float((st.get("bar") or {}).get("open") or 0)
                bar_close = float((st.get("bar") or {}).get("close") or 0)
                ref = bar_close if bar_close > 0 else bar_open
                # pulse 源只做 close 跟随，不因 1.5% 过滤导致 quote 与 K 线脱节
                is_pulse = "pulse" in src
                max_dev = 0.05 if is_pulse else 0.015
                if ref > 0 and not _price_sane(ref, last_price, max_dev=max_dev):
                    continue
                _update_state(
                    st, is_daily, bar_time, last_price, total_volume, position, quote
                )

            key = realtime_kline_key("virtual", symbol, period)
            await redis_conn.set(
                key,
                json.dumps(
                    {
                        "symbol": symbol,
                        "period": period,
                        "trading_mode": "virtual",
                        "bar": st["bar"],
                    },
                    ensure_ascii=False,
                ),
                ex=_TTL_REALTIME,
            )
            updated.append(
                {
                    "symbol": symbol,
                    "period": period,
                    "trading_mode": "virtual",
                    "bar": st["bar"],
                }
            )

    return updated


def _new_state(
    is_daily: bool,
    bar_time: str,
    last_price: float,
    total_volume: int,
    position: Any,
    quote: dict[str, Any],
) -> dict[str, Any]:
    # 分钟/日线 forming 都只用 last 滚动 OHLC，不用 CTP 会话 high/low
    # （会话 high/low 在串月污染时会被拉飞）
    _ = quote
    bar = {
        "time": bar_time,
        "open": last_price,
        "high": last_price,
        "low": last_price,
        "close": last_price,
        "volume": total_volume if is_daily else 0,
        "settle": None,
        "open_interest": int(position) if position is not None else None,
        "source": "virtual",
    }
    return {
        "bar": bar,
        "bar_time": bar_time,
        "period_start_volume": total_volume,
    }


def _update_state(
    st: dict[str, Any],
    is_daily: bool,
    bar_time: str,
    last_price: float,
    total_volume: int,
    position: Any,
    quote: dict[str, Any],
) -> None:
    _ = quote
    bar = st["bar"]
    bar["time"] = bar_time
    st["bar_time"] = bar_time
    bar["close"] = last_price
    bar["high"] = max(float(bar.get("high") or last_price), last_price)
    bar["low"] = min(float(bar.get("low") or last_price), last_price)
    bar["source"] = "virtual"
    if position is not None:
        bar["open_interest"] = int(position)
    if is_daily:
        bar["volume"] = total_volume
        return
    prev = int(st.get("period_start_volume") or 0)
    delta = total_volume - prev
    if delta < 0:
        st["period_start_volume"] = total_volume
        bar["volume"] = 0
    else:
        bar["volume"] = delta
