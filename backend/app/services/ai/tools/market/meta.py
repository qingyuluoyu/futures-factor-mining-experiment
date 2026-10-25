"""主力合约、持仓序列与市场快照工具"""

from __future__ import annotations

import asyncio
from typing import Any

import redis.asyncio as aioredis

from app.services.ai.tools.market import tool_get_kline, tool_get_quote
from app.services.contract_master import get_master_contracts

_MAX_OI_POINTS = 80


async def tool_get_open_interest_series(
    symbol: str,
    period: str,
    limit: int | None,
    redis_conn: aioredis.Redis | None,
) -> dict[str, Any]:
    """从 K 线抽取成交量/持仓量/结算价序列（已含实时合并）。"""
    kline_result = await tool_get_kline(symbol, period, limit, redis_conn)
    if not kline_result.get("success"):
        return kline_result

    bars = kline_result.get("bars") or []
    series: list[dict[str, Any]] = []
    for bar in bars[-_MAX_OI_POINTS:]:
        series.append(
            {
                "time": bar.get("time"),
                "close": bar.get("close"),
                "volume": bar.get("volume"),
                "open_interest": bar.get("open_interest"),
                "settle": bar.get("settle"),
            }
        )

    oi_values = [
        item["open_interest"]
        for item in series
        if item.get("open_interest") is not None
    ]
    vol_values = [
        item["volume"] for item in series if item.get("volume") is not None
    ]
    oi_change = None
    if len(oi_values) >= 2:
        oi_change = oi_values[-1] - oi_values[0]
    return {
        "success": True,
        "symbol": kline_result.get("symbol"),
        "period": kline_result.get("period"),
        "as_of": kline_result.get("as_of"),
        "freshness": kline_result.get("freshness"),
        "point_count": len(series),
        "oi_available": len(oi_values) > 0,
        "volume_sum": int(sum(vol_values)) if vol_values else 0,
        "oi_last": oi_values[-1] if oi_values else None,
        "oi_change": oi_change,
        "series": series,
        "note": "末根已合并秒级 quote 持仓；分钟 settle 常为空",
    }


async def tool_get_master_contract(
    code: str,
    redis_conn: aioredis.Redis | None,
) -> dict[str, Any]:
    """查询品种主力/次主力合约。"""
    code_clean = code.strip().upper()
    if not code_clean:
        return {"success": False, "error": "code 不能为空，例如 RB/AU/IF"}
    if redis_conn is None:
        return {"success": False, "error": "Redis 不可用，无法读取主力合约缓存"}

    masters = await get_master_contracts(redis_conn)
    if not masters:
        return {
            "success": False,
            "error": "主力合约缓存为空，请稍后重试（调度器可能尚未完成识别）",
        }

    item = masters.get(code_clean)
    if item is None:
        available = sorted(masters.keys())[:30]
        return {
            "success": False,
            "error": f"未找到品种 {code_clean}",
            "available_codes_sample": available,
        }
    return {
        "success": True,
        "code": code_clean,
        "master": item.get("master"),
        "secondary": item.get("secondary"),
        "updated_at": item.get("updated_at"),
    }


async def tool_get_market_snapshot(
    symbol: str,
    period: str,
    kline_limit: int | None,
    redis_conn: aioredis.Redis | None,
) -> dict[str, Any]:
    """复合快照：行情 + 最近 K 线摘要 + 主力信息。"""
    symbol_clean = symbol.strip()
    period_clean = period.strip() if period else "5m"
    if not symbol_clean:
        return {"success": False, "error": "symbol 不能为空"}

    quote_result, kline_result = await asyncio.gather(
        tool_get_quote(symbol_clean, redis_conn),
        tool_get_kline(symbol_clean, period_clean, kline_limit, redis_conn),
    )

    code_chars = "".join(ch for ch in symbol_clean if ch.isalpha()).upper()
    master_info: dict[str, Any] | None = None
    if code_chars and redis_conn is not None:
        master_result = await tool_get_master_contract(code_chars, redis_conn)
        if master_result.get("success"):
            master_info = master_result

    quote_ok = bool(quote_result.get("success"))
    kline_ok = bool(kline_result.get("success"))
    bars_tail = (kline_result.get("bars") or [])[-10:] if kline_ok else []
    return {
        "success": True,
        "symbol": symbol_clean,
        "period": period_clean,
        "quote": quote_result.get("quote") if quote_ok else None,
        "quote_error": None if quote_ok else quote_result.get("error"),
        "kline_summary": kline_result.get("summary") if kline_ok else None,
        "kline_bars_tail": bars_tail,
        "kline_error": None if kline_ok else kline_result.get("error"),
        "master": master_info,
    }
