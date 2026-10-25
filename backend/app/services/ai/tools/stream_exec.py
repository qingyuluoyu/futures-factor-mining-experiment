"""可流式工具执行 —— 中途 yield SSE（子代理状态等）"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from app.services.ai.providers.base import StreamEvent

logger = logging.getLogger("qihuo.ai.stream_exec")

# 支持中途推送进度的工具名
STREAMABLE_TOOLS: frozenset[str] = frozenset({"research_products"})


def is_streamable_tool(name: str) -> bool:
    """是否走流式执行路径。"""
    return name in STREAMABLE_TOOLS


async def run_streamable_tool(
    name: str,
    arguments: dict[str, Any],
    tool_call_id: str,
    redis_conn: Any,
) -> AsyncIterator[StreamEvent]:
    """执行流式工具：yield subagent_* 与最终 tool_result。"""
    if name == "research_products":
        from app.services.ai.research import research_products_stream

        async for event in research_products_stream(
            arguments,
            redis_conn,
            tool_call_id,
        ):
            yield event
        return

    # 未知流式工具：返回错误 tool_result
    yield StreamEvent(
        type="tool_result",
        content=json.dumps(
            {"success": False, "error": f"不支持流式工具: {name}"},
            ensure_ascii=False,
        ),
        tool_name=name,
        tool_call_id=tool_call_id,
    )
