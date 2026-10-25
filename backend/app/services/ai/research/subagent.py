"""单批子代理 —— 拉取 K 线/报价并技术面打分"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.ai.research.signals import score_product
from app.services.ai.research.types import BatchResult, ProductRef, ProductSignal
from app.services.ai.tools.market import tool_get_kline, tool_get_quote

logger = logging.getLogger("qihuo.ai.research.subagent")

# 批内同时调研的品种数上限
_INNER_CONCURRENCY = 4
_KLINE_LIMIT = 80


async def _analyze_one(
    product: ProductRef,
    period: str,
    redis_conn: Any,
    sem: asyncio.Semaphore,
) -> ProductSignal | None:
    """分析单个品种；失败返回 None（调用方计 error）。"""
    async with sem:
        symbol = product["symbol"]
        code = product["product_code"]
        name = product.get("product_name") or code
        try:
            kline_task = tool_get_kline(symbol, period, _KLINE_LIMIT, redis_conn)
            quote_task = tool_get_quote(symbol, redis_conn)
            kline_result, quote_result = await asyncio.gather(
                kline_task, quote_task, return_exceptions=True
            )
        except Exception as exc:
            logger.warning("品种 %s 拉取失败: %s", symbol, exc)
            return None

        if isinstance(kline_result, Exception):
            logger.warning("K线失败 %s: %s", symbol, kline_result)
            return None
        if not isinstance(kline_result, dict) or not kline_result.get("success"):
            return None

        bars = kline_result.get("bars") or []
        quote: dict[str, Any] | None = None
        if isinstance(quote_result, dict) and quote_result.get("success"):
            quote = quote_result

        return score_product(code, symbol, name, bars, quote)


async def run_subagent_batch(
    agent_id: str,
    agent_name: str,
    products: list[ProductRef],
    period: str,
    redis_conn: Any,
) -> BatchResult:
    """执行一批品种调研，过滤由 orchestrator 做也可在此统计。"""
    sem = asyncio.Semaphore(_INNER_CONCURRENCY)
    tasks = [
        _analyze_one(product, period, redis_conn, sem) for product in products
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    signals: list[ProductSignal] = []
    error_count = 0
    for item in results:
        if isinstance(item, Exception) or item is None:
            error_count += 1
            continue
        signals.append(item)

    long_count = sum(1 for s in signals if s["bias"] == "long")
    short_count = sum(1 for s in signals if s["bias"] == "short")
    range_count = sum(1 for s in signals if s["bias"] == "range")

    return {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "signals": signals,
        "long_count": long_count,
        "short_count": short_count,
        "range_count": range_count,
        "error_count": error_count,
        "error": None,
    }
