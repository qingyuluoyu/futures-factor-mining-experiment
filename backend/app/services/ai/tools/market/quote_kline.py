"""行情 Agent 工具：quote / kline（合并秒级实时）"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Any

import redis.asyncio as aioredis

from app.data.sina_kline import SUPPORTED_PERIODS
from app.services.kline import get_kline_history
from app.services.kline_keys import realtime_kline_key
from app.services.market_keys import read_candidate_quote_keys
from app.services.ai.tools.market.scoped_cache import InvalidMarketCache, compatible_virtual_quote, observed_virtual_history, valid_quote, validate_virtual_bar

logger = logging.getLogger("qihuo.ai.tools_market")
_BJ = timezone(timedelta(hours=8))


def _limit(limit: int | None, default: int, maximum: int) -> int:
    """规范化 limit。"""
    try:
        value = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        return default
    return default if value <= 0 else min(value, maximum)


def _f(value: Any) -> float | None:
    """安全 float。"""
    try:
        result = None if value is None or isinstance(value, bool) else float(value)
        return result if result is not None and math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int | None:
    """安全 int。"""
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _slim(bar: dict[str, Any]) -> dict[str, Any]:
    """精简 bar。"""
    return {
        "time": bar.get("time"),
        "open": bar.get("open"),
        "high": bar.get("high"),
        "low": bar.get("low"),
        "close": bar.get("close"),
        "volume": bar.get("volume"),
        "settle": bar.get("settle"),
        "open_interest": bar.get("open_interest"),
    }


def _overlay_quote(bars: list[dict[str, Any]], quote: dict[str, Any] | None) -> bool:
    """用 quote 刷新末根价与持仓。"""
    if not bars or not quote:
        return False
    last = bars[-1]
    price = _f(quote.get("last_price"))
    if price is not None:
        last["close"] = price
        high, low = _f(last.get("high")), _f(last.get("low"))
        if high is None or price > high:
            last["high"] = price
        if low is None or price < low:
            last["low"] = price
    pos = _i(quote.get("position"))
    if pos is not None:
        last["open_interest"] = pos
    return price is not None or pos is not None


def _merge(
    history: list[dict[str, Any]],
    rt_bar: dict[str, Any] | None,
    quote: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool, str]:
    """合并 forming bar；兼容 time 标签不一致。"""
    bars = [_slim(item) for item in history]
    mode, done = "none", False
    oi = _i(quote.get("position")) if quote else None
    if rt_bar is not None:
        rt = _slim(rt_bar)
        if oi is not None and rt.get("open_interest") is None:
            rt["open_interest"] = oi
        if not bars:
            bars, done, mode = [rt], True, "append"
        else:
            lt, rt_t = str(bars[-1].get("time") or ""), str(rt.get("time") or "")
            if rt_t and lt == rt_t:
                last = bars[-1]
                bars[-1] = {
                    **last,
                    "open": rt.get("open", last.get("open")),
                    "high": rt.get("high", last.get("high")),
                    "low": rt.get("low", last.get("low")),
                    "close": rt.get("close", last.get("close")),
                    "volume": rt.get("volume", last.get("volume")),
                    "settle": rt.get("settle") if rt.get("settle") is not None else last.get("settle"),
                    "open_interest": (
                        rt.get("open_interest")
                        if rt.get("open_interest") is not None
                        else last.get("open_interest")
                    ),
                }
                done, mode = True, "equal"
            elif rt_t and lt < rt_t:
                bars.append(rt)
                done, mode = True, "append"
            elif rt_t and lt > rt_t:
                last = bars[-1]
                close = _f(rt.get("close"))
                if close is not None:
                    last["close"] = close
                    high, low = _f(last.get("high")), _f(last.get("low"))
                    if high is None or close > high:
                        last["high"] = close
                    if low is None or close < low:
                        last["low"] = close
                if oi is not None:
                    last["open_interest"] = oi
                done, mode = True, "label_mismatch"
    if _overlay_quote(bars, quote):
        done = True
        if mode == "none":
            mode = "quote_only"
    return bars, done, mode


def _sum_quote(quote: dict[str, Any]) -> dict[str, Any]:
    """精简 quote。"""
    return {
        "symbol": quote.get("symbol"),
        "name": quote.get("name"),
        "exchange": quote.get("exchange"),
        "last_price": quote.get("last_price"),
        "change": quote.get("change"),
        "change_pct": quote.get("change_pct"),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "pre_settle": quote.get("pre_close"),
        "bid_price": quote.get("bid_price"),
        "ask_price": quote.get("ask_price"),
        "volume": quote.get("volume"),
        "open_interest": quote.get("position"),
        "tick_time": quote.get("tick_time"),
        "decimal_places": quote.get("decimal_places"),
        "source_note": "pre_settle=昨结算; open_interest=持仓; 缓存更新时间需独立核验",
        "data_source": quote.get("source"),
    }


async def _redis_json(
    redis_conn: aioredis.Redis | None,
    keys: list[str],
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    """按候选 key 读 JSON dict。"""
    if redis_conn is None:
        return None
    for key in keys:
        raw = await redis_conn.get(key)
        if raw is None or (not raw and not strict):
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            if strict:
                raise InvalidMarketCache("本盘行情缓存无法解析") from None
            continue
        if isinstance(data, dict):
            return data
        if strict:
            raise InvalidMarketCache("本盘行情缓存不是对象")
    return None


async def _read_quote(redis_conn: aioredis.Redis | None, symbol: str, trading_mode: str = "live") -> dict[str, Any] | None:
    """读行情缓存。"""
    s = symbol.lower().strip()
    return await _redis_json(redis_conn, read_candidate_quote_keys(trading_mode, s), strict=trading_mode == "virtual")


async def _read_rt(
    redis_conn: aioredis.Redis | None,
    symbol: str,
    period: str,
    trading_mode: str = "live",
) -> dict[str, Any] | None:
    """读实时 bar。"""
    s = symbol.lower().strip()
    payload = await _redis_json(
        redis_conn,
        [realtime_kline_key(trading_mode, s, period)],
        strict=trading_mode == "virtual",
    )
    if payload is None:
        return None
    bar = payload.get("bar")
    if trading_mode == "virtual" and (not isinstance(bar, dict) or bar.get("time") is None):
        raise InvalidMarketCache("虚拟forming缓存缺少有效bar和时间")
    return bar if isinstance(bar, dict) and bar.get("time") is not None else None


async def tool_get_quote(
    symbol: str,
    redis_conn: aioredis.Redis | None,
    *,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """实时行情（Redis 优先，调度约 1 秒）。"""
    symbol_clean = symbol.strip()
    if not symbol_clean:
        return {"success": False, "error": "symbol 不能为空"}
    as_of = datetime.now(_BJ).isoformat(timespec="seconds")
    if trading_mode not in {"live", "virtual"}:
        return {"success": False, "error_code": "invalid_market_scope", "error": "不支持的行情盘"}
    try:
        cached = await _read_quote(redis_conn, symbol_clean, trading_mode)
    except InvalidMarketCache as error:
        return {"success": False, "error_code": "invalid_market_data", "error": str(error)}
    if cached is not None:
        if not valid_quote(cached):
            return {"success": False, "error_code": "invalid_market_data", "error": "报价价格或数值字段无效"}
        if trading_mode == "virtual" and not compatible_virtual_quote(cached):
            return {"success": False, "error_code": "incompatible_market_source", "error": "虚拟报价来自live补价、模拟脉冲或未证明来源，未作为本盘观测报价返回"}
        return {
            "success": True,
            "source": "redis",
            "as_of": as_of,
            "freshness": "cache_timestamp_unverified",
            "trading_mode": trading_mode, "market_type": "futures", "read_at": as_of,
            "quote": _sum_quote(cached),
        }
    if trading_mode == "virtual":
        return {"success": False, "error_code": "missing_market_data", "error": "虚拟盘报价缓存缺失；未使用live或新浪补价", "trading_mode": trading_mode}
    from app.connectors.sina.client import fetch_quote_via_bridge

    quote = await asyncio.to_thread(fetch_quote_via_bridge, symbol_clean)
    if quote is None:
        return {"success": False, "error": f"未获取到行情: {symbol_clean}"}
    if not valid_quote(quote):
        return {"success": False, "error_code": "invalid_market_data", "error": "供应商报价缺少有限正价格"}
    return {
        "success": True,
        "source": "sina",
        "as_of": as_of,
        "freshness": "sina_bridge_fetch",
        "trading_mode": trading_mode, "market_type": "futures", "read_at": as_of,
        "quote": _sum_quote(quote),
    }


async def tool_get_kline(
    symbol: str,
    period: str,
    limit: int | None,
    redis_conn: aioredis.Redis | None,
    *,
    trading_mode: str = "live",
) -> dict[str, Any]:
    """K 线 = 历史 + 实时 forming + quote 覆盖最新价/持仓。"""
    symbol_clean, period_clean = symbol.strip(), period.strip()
    if not symbol_clean:
        return {"success": False, "error": "symbol 不能为空"}
    if period_clean not in SUPPORTED_PERIODS:
        return {
            "success": False,
            "error": f"不支持的周期: {period_clean}，可选: {', '.join(SUPPORTED_PERIODS)}",
        }
    bar_limit = _limit(limit, 50, 200)
    as_of = datetime.now(_BJ).isoformat(timespec="seconds")
    bars: list[dict[str, Any]] = []
    source = "sina_direct"
    excluded_seed_bars = 0
    if trading_mode not in {"live", "virtual"}:
        return {"success": False, "error_code": "invalid_market_scope", "error": "不支持的行情盘"}
    if trading_mode == "virtual":
        try:
            bars, excluded_seed_bars = await observed_virtual_history(redis_conn, symbol_clean, period_clean)
        except InvalidMarketCache as error:
            return {"success": False, "error_code": "invalid_market_data", "error": str(error)}
        bars = bars[-bar_limit:]
        source = "virtual_namespace_cache"
    elif redis_conn is not None:
        bars = await get_kline_history(
            redis_conn, symbol_clean, period_clean, limit=bar_limit, end_time="", session=None, trading_mode=trading_mode
        )
        source = "kline_service"
    else:
        # 无 Redis：走多数据源统一入口（主渠道优先，失败自动降级备用渠道）
        from app.data.kline_provider import fetch_kline_with_fallback

        bars = await fetch_kline_with_fallback(
            None, symbol_clean, period_clean
        )
        if bars:
            bars = bars[-bar_limit:]

    try:
        quote = await _read_quote(redis_conn, symbol_clean, trading_mode)
        rt = await _read_rt(redis_conn, symbol_clean, period_clean, trading_mode)
    except InvalidMarketCache as error:
        return {"success": False, "error_code": "invalid_market_data", "error": str(error)}
    if trading_mode == "virtual" and quote is not None and not valid_quote(quote):
        return {"success": False, "error_code": "invalid_market_data", "error": "虚拟报价价格无效，不能作为行情摘要"}
    if trading_mode == "virtual" and quote is not None and not compatible_virtual_quote(quote):
        quote = None
    if trading_mode == "virtual" and rt is not None:
        try:
            rt = validate_virtual_bar(rt, period_clean)
        except InvalidMarketCache as error:
            return {"success": False, "error_code": "invalid_market_data", "error": str(error)}
        if bars and rt["time"] <= bars[-1]["time"]:
            rt = None
    slim, merged, mode = _merge(bars, rt, quote if trading_mode == "live" else None)
    if bar_limit > 0 and len(slim) > bar_limit:
        slim = slim[-bar_limit:]
    if not slim:
        return {"success": False, "error_code": "missing_market_data", "error": f"未获取到本盘 K 线: {symbol_clean} {period_clean}"}
    first, last = slim[0], slim[-1]
    live = quote.get("last_price") if quote else last.get("close") if trading_mode == "live" else None
    return {
        "success": True,
        "source": source,
        "trading_mode": trading_mode, "market_type": "futures", "read_at": as_of,
        "excluded_seed_or_unknown_bars": excluded_seed_bars,
        "quote_overlay_applied": trading_mode == "live" and quote is not None,
        "origin_verification": "archive_tick_origin_not_preserved" if trading_mode == "virtual" else "source_reader_not_certified",
        "usage_limit": "仅模拟盘展示；既有virtual归档未保留逐笔来源，可能包含模拟脉冲，不作为真实市场或策略有效性证据" if trading_mode == "virtual" else "供应商时点及来源仍需独立核验",
        "as_of": as_of,
        "realtime_merged": merged,
        "merge_mode": mode,
        "freshness": "cache_timestamp_unverified" if trading_mode == "virtual" else "history+realtime+quote_~1s" if merged else "history_only",
        "symbol": symbol_clean,
        "period": period_clean,
        "limit": bar_limit,
        "summary": {
            "bar_count": len(slim),
            "first_time": first.get("time"),
            "last_time": last.get("time"),
            "last_close": last.get("close"),
            "live_last_price": live,
            "last_volume": last.get("volume"),
            "last_open_interest": last.get("open_interest"),
            "last_settle": last.get("settle"),
            "realtime_merged": merged,
            "merge_mode": mode,
        },
        "bars": slim,
    }
