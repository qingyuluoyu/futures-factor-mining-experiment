"""行情 AI 工具：盘口五档 + 成交统计（供 AI 分析买卖力量）"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.services.market_keys import get_quote_json, orderbook_key

logger = logging.getLogger("qihuo.ai.tools_market")


def _redis_json(raw: Any) -> dict[str, Any] | None:
    """Redis 原始值安全转 JSON dict。"""
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _slim_levels(levels: Any) -> list[dict[str, Any]] | None:
    """档位列表瘦身：只留 price/volume。"""
    if not isinstance(levels, list) or not levels:
        return None
    out: list[dict[str, Any]] = []
    for item in levels:
        if not isinstance(item, dict):
            continue
        price, volume = item.get("price"), item.get("volume")
        if price is None or volume is None:
            continue
        out.append({"price": price, "volume": volume})
    return out or None


async def tool_get_orderbook(
    redis_conn: aioredis.Redis | None,
    symbol: str,
) -> dict[str, Any]:
    """盘口数据：优先读统一盘口面板缓存（5档+涨跌/量/持仓/内外盘统计），
    缺失时回退实时报价（买卖一档+基本字段）。"""
    sym = symbol.strip().lower()
    if not sym:
        return {"success": False, "error": "缺少 symbol 参数"}
    if redis_conn is None:
        return {"success": False, "error": "行情缓存不可用"}

    # 盘口面板缓存（build_market_panel 产物）
    try:
        panel = _redis_json(await redis_conn.get(orderbook_key(None, sym)))
    except Exception as exc:
        logger.warning("盘口工具读面板缓存失败 symbol=%s err=%s", sym, exc)
        panel = None

    if panel:
        stats_raw = panel.get("stats") if isinstance(panel.get("stats"), dict) else {}
        # stats 每项 {value, source, quality} → 压平为 {name: value}
        stats = {
            name: (item.get("value") if isinstance(item, dict) else item)
            for name, item in stats_raw.items()
        }
        return {
            "success": True,
            "symbol": sym,
            "asof": panel.get("asof"),
            "stale": bool(panel.get("stale")),
            "asks": _slim_levels(panel.get("asks")) or [],
            "bids": _slim_levels(panel.get("bids")) or [],
            "stats": stats,
        }

    # 回退：实时报价（一档）
    quote = await get_quote_json(redis_conn, None, sym)
    if not quote:
        return {"success": False, "error": f"未找到 {sym} 的盘口/报价数据"}
    return {
        "success": True,
        "symbol": sym,
        "source": "quote",
        "asks": [{"price": quote.get("ask_price"), "volume": quote.get("ask_vol")}]
        if quote.get("ask_price") is not None
        else [],
        "bids": [{"price": quote.get("bid_price"), "volume": quote.get("bid_vol")}]
        if quote.get("bid_price") is not None
        else [],
        "stats": {
            "last_price": quote.get("last_price"),
            "volume": quote.get("volume"),
            "open_interest": quote.get("position"),
        },
    }
