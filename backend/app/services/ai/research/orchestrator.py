"""多品种调研编排 —— 分批、并发子代理、流式 SSE 事件、汇总 JSON"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from app.services.ai.providers.base import StreamEvent
from app.services.ai.research.chunking import (
    chunk_list,
    clamp_batch_size,
    clamp_max_products,
    list_master_products,
    make_agent_name,
)
from app.services.ai.research.format_report import build_report_markdown
from app.services.ai.research.subagent import run_subagent_batch
from app.services.ai.research.types import (
    BatchResult,
    ProductRef,
    ProductSignal,
    ResearchSummary,
    SubAgentPlanItem,
)

logger = logging.getLogger("qihuo.ai.research.orchestrator")

_SUBAGENT_CONCURRENCY = 3


def _event(
    event_type: str,
    *,
    content: str = "",
    tool_name: str = "",
    tool_args: str = "",
    tool_call_id: str = "",
    extra: dict[str, Any] | None = None,
) -> StreamEvent:
    """构造 StreamEvent；extra 供 ai_chat 展开序列化。"""
    return StreamEvent(
        type=event_type,
        content=content,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_call_id=tool_call_id,
        extra=extra if extra is not None else {},
    )


def _build_summary(
    all_signals: list[ProductSignal],
    agent_count: int,
    errors: list[str],
    total_products: int,
) -> ResearchSummary:
    """过滤震荡，生成主代理可读汇总。"""
    long_list = [s for s in all_signals if s["bias"] == "long"]
    short_list = [s for s in all_signals if s["bias"] == "short"]
    range_count = sum(1 for s in all_signals if s["bias"] == "range")
    long_list = sorted(long_list, key=lambda s: s["confidence"], reverse=True)
    short_list = sorted(short_list, key=lambda s: s["confidence"], reverse=True)
    report = build_report_markdown(long_list, short_list, range_count)
    return {
        "success": True,
        "total_products": total_products,
        "long": long_list,
        "short": short_list,
        "range_filtered": range_count,
        "errors": errors[:30],
        "agent_count": agent_count,
        "note": (
            "技术面默认包结果：含方向、入场点、支撑/压力、止损、止盈。"
            "请优先照抄 report_markdown 中的数字表格；禁止模糊表述。"
            "仅供参考，不构成投资建议。"
        ),
        "report_markdown": report,
    }


def _fail_payload(message: str) -> str:
    """失败 tool_result JSON。"""
    return json.dumps(
        {
            "success": False,
            "error": message,
            "long": [],
            "short": [],
            "range_filtered": 0,
            "errors": [message],
            "agent_count": 0,
            "note": "",
            "total_products": 0,
            "report_markdown": f"调研失败：{message}",
        },
        ensure_ascii=False,
    )


async def research_products_stream(
    arguments: dict[str, Any],
    redis_conn: Any,
    tool_call_id: str,
) -> AsyncIterator[StreamEvent]:
    """流式执行全市场/多品种调研。

    事件顺序：subagent_plan → subagent_update* → tool_result
    """
    scope_raw = arguments.get("scope", "all")
    period = str(arguments.get("period") or "1d").strip() or "1d"
    if period not in ("1d", "60m", "30m", "15m", "5m"):
        period = "1d"
    batch_size = clamp_batch_size(arguments.get("batch_size"))
    max_products = clamp_max_products(arguments.get("max_products"))

    products, err = await list_master_products(redis_conn, scope_raw, max_products)
    if err or not products:
        yield _event(
            "tool_result",
            content=_fail_payload(err or "无品种"),
            tool_name="research_products",
            tool_call_id=tool_call_id,
        )
        return

    batches = chunk_list(products, batch_size)
    plan: list[SubAgentPlanItem] = []
    for index, batch in enumerate(batches, start=1):
        plan.append(
            {
                "id": f"sa-{index}",
                "name": make_agent_name(index, batch),
                "symbols": [p["symbol"] for p in batch],
                "product_codes": [p["product_code"] for p in batch],
                "status": "pending",
            }
        )

    yield _event(
        "subagent_plan",
        extra={"agents": plan, "total": len(plan)},
    )

    queue: asyncio.Queue[StreamEvent | object] = asyncio.Queue()
    sentinel = object()
    batch_results: list[BatchResult] = []
    result_lock = asyncio.Lock()

    async def _run_one(
        item: SubAgentPlanItem,
        batch_products: list[ProductRef],
        gate: asyncio.Semaphore,
    ) -> None:
        agent_id = item["id"]
        agent_name = item["name"]
        async with gate:
            await queue.put(
                _event(
                    "subagent_update",
                    extra={
                        "id": agent_id,
                        "name": agent_name,
                        "status": "running",
                        "detail": f"调研中，共 {len(batch_products)} 个品种",
                        "progress": 0.05,
                        "long_count": 0,
                        "short_count": 0,
                        "error": None,
                    },
                )
            )
            try:
                result = await run_subagent_batch(
                    agent_id,
                    agent_name,
                    batch_products,
                    period,
                    redis_conn,
                )
                async with result_lock:
                    batch_results.append(result)
                total = max(len(batch_products), 1)
                done_n = len(result["signals"])
                await queue.put(
                    _event(
                        "subagent_update",
                        extra={
                            "id": agent_id,
                            "name": agent_name,
                            "status": "done",
                            "detail": (
                                f"完成 {done_n}/{total}，"
                                f"做多{result['long_count']} "
                                f"做空{result['short_count']} "
                                f"震荡{result['range_count']}"
                            ),
                            "progress": 1.0,
                            "long_count": result["long_count"],
                            "short_count": result["short_count"],
                            "error": None,
                        },
                    )
                )
            except Exception as exc:
                logger.exception("子代理 %s 失败", agent_id)
                async with result_lock:
                    batch_results.append(
                        {
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "signals": [],
                            "long_count": 0,
                            "short_count": 0,
                            "range_count": 0,
                            "error_count": len(batch_products),
                            "error": str(exc)[:200],
                        }
                    )
                await queue.put(
                    _event(
                        "subagent_update",
                        extra={
                            "id": agent_id,
                            "name": agent_name,
                            "status": "error",
                            "detail": f"失败：{str(exc)[:80]}",
                            "progress": 1.0,
                            "long_count": 0,
                            "short_count": 0,
                            "error": str(exc)[:200],
                        },
                    )
                )

    async def _producer() -> None:
        gate = asyncio.Semaphore(_SUBAGENT_CONCURRENCY)
        tasks = [
            asyncio.create_task(_run_one(plan[i], batches[i], gate))
            for i in range(len(batches))
        ]
        await asyncio.gather(*tasks)
        await queue.put(sentinel)

    producer = asyncio.create_task(_producer())
    while True:
        item = await queue.get()
        if item is sentinel:
            break
        if isinstance(item, StreamEvent):
            yield item
    await producer

    all_signals: list[ProductSignal] = []
    errors: list[str] = []
    for result in batch_results:
        all_signals.extend(result["signals"])
        if result["error_count"] > 0:
            errors.append(
                f"{result['agent_name']}: {result['error_count']} 个品种数据失败"
            )
        if result.get("error"):
            errors.append(f"{result['agent_name']}: {result['error']}")

    summary = _build_summary(
        all_signals,
        len(plan),
        errors,
        len(products),
    )
    yield _event(
        "tool_result",
        content=json.dumps(summary, ensure_ascii=False, default=str),
        tool_name="research_products",
        tool_call_id=tool_call_id,
    )


async def research_products_sync(
    arguments: dict[str, Any],
    redis_conn: Any,
) -> str:
    """非流式：跑完编排，只返回汇总 JSON 字符串。"""
    final = ""
    async for event in research_products_stream(
        arguments,
        redis_conn,
        tool_call_id="sync",
    ):
        if event.type == "tool_result":
            final = event.content
    if not final:
        return _fail_payload("调研未产生结果")
    return final
