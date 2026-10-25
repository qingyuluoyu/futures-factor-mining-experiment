"""工具调用编排 —— 并行执行、重试、消息构造

从 gateway 拆出，保持网关文件在行数限制内。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.tools import execute_tool
from app.services.ai.execution_context import ExecutionContext
from app.services.ai.tools.safety import READ_RETRY_TOOLS

logger = logging.getLogger("qihuo.ai.tool_loop")

TOOL_RETRY_COUNT = 1


async def execute_tool_with_retry(
    name: str,
    arguments: dict[str, Any],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: Any | None,
    indicator_config: dict[str, Any] | None = None,
    execution_context: ExecutionContext | None = None,
) -> str:
    """仅对白名单只读工具重试超时；写或未知工具不自动重放。"""
    retries = TOOL_RETRY_COUNT if session is None and name in READ_RETRY_TOOLS else 0
    for attempt in range(1 + retries):
        try:
            return await execute_tool(
                name,
                arguments,
                session,
                user_id,
                redis_conn,
                indicator_config,
                execution_context=execution_context,
            )
        except (asyncio.TimeoutError, TimeoutError):
            if attempt < retries:
                logger.info("工具 %s 超时，正在重试...", name)
                continue
            return json.dumps(
                {"error": f"工具 {name} 执行超时", "success": False},
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                {
                    "error": f"工具 {name} 执行失败",
                    "error_code": "tool_execution_failed",
                    "success": False,
                },
                ensure_ascii=False,
            )
    return ""


def build_tool_result_message(
    tool_call_id: str,
    tool_name: str,
    result: str,
    api_type: str,
) -> dict[str, Any]:
    """构造工具结果消息。"""
    if api_type == "claude":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result,
                }
            ],
        }
    return {"role": "tool", "tool_call_id": tool_call_id, "content": result}


def build_assistant_tool_call_message(
    tool_calls: list[dict[str, Any]],
    api_type: str,
) -> dict[str, Any]:
    """构造助手工具调用消息。"""
    if api_type == "claude":
        content = []
        for tool_call in tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tool_call["tool_call_id"],
                    "name": tool_call["tool_name"],
                    "input": tool_call.get("tool_args_parsed", {}),
                }
            )
        return {"role": "assistant", "content": content}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tool_call["tool_call_id"],
                "type": "function",
                "function": {
                    "name": tool_call["tool_name"],
                    "arguments": tool_call["tool_args"],
                },
            }
            for tool_call in tool_calls
        ],
    }


async def execute_tools_parallel(
    tool_calls: list[dict[str, Any]],
    session: AsyncSession | None,
    user_id: str | None,
    redis_conn: Any | None,
    indicator_config: dict[str, Any] | None = None,
    execution_context: ExecutionContext | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """有共享数据库会话时顺序执行，独立的无状态只读调用才并行。"""
    tasks = []
    prepared = []
    for tool_call in tool_calls:
        try:
            args_parsed = (
                json.loads(tool_call["tool_args"]) if tool_call["tool_args"] else {}
            )
            if not isinstance(args_parsed, dict):
                raise ValueError("Tool arguments must be an object")
        except (json.JSONDecodeError, TypeError, ValueError):
            prepared.append((tool_call, json.dumps({"success": False, "error_code": "invalid_tool_arguments", "error": "工具参数必须是有效JSON对象"}, ensure_ascii=False)))
            continue
        tool_call["tool_args_parsed"] = args_parsed
        execution = execute_tool_with_retry(
            tool_call["tool_name"],
            args_parsed,
            session,
            user_id,
            redis_conn,
            indicator_config,
            execution_context=execution_context,
        )
        if session is not None or tool_call["tool_name"] not in READ_RETRY_TOOLS:
            prepared.append((tool_call, await execution))
        else:
            tasks.append(execution)
            prepared.append((tool_call, None))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    parallel_results = iter(results)
    output: list[tuple[dict[str, Any], str]] = []
    for tool_call, result in prepared:
        if result is None:
            result = next(parallel_results)
        if isinstance(result, Exception):
            result_str = json.dumps(
                {"error": "工具执行失败", "success": False},
                ensure_ascii=False,
            )
        else:
            result_str = result
        output.append((tool_call, result_str))
    return output


def strip_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去除消息中的图片内容。"""
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            filtered = [item for item in content if item.get("type") != "image_url"]
            if filtered:
                cleaned.append({**msg, "content": filtered})
        else:
            cleaned.append(msg)
    return cleaned
