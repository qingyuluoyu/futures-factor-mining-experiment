"""OpenAI 规范 Provider —— 兼容 OpenAI/DeepSeek/通义千问等

工具调用在拿到 name 时即向前端发出 tool_call（loading），
参数增量继续拼接，流结束时再发完整参数。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.services.ai.providers.base import (
    AIProviderBase,
    StreamEvent,
    ToolDef,
)
from app.services.ai.providers.urls import resolve_endpoint


def provider_error(payload: Any, status: int = 0, retry_after: str = "") -> StreamEvent:
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    marker = str(error).lower() if isinstance(error, (dict, str)) else ""
    code = "provider_unavailable"
    message = "模型服务暂时不可用，请稍后重试；本次未自动重试。"
    if status in (401, 403):
        code, message = "provider_auth_failed", "模型服务拒绝认证或权限，请检查当前渠道、密钥及模型授权。"
    elif status == 402 or "insufficient_quota" in marker:
        code, message = "provider_quota_exhausted", "模型服务额度不足，请核对供应商账户与模型可用额度；本次未自动重试。"
    elif status == 429 or any(word in marker for word in ("rate_limit", "ratelimit", "tpm", "rpm")):
        code, message = "provider_rate_limited", "模型供应商触发 TPM/RPM 速率限制（每分钟 token/请求数），不代表密钥无效或余额不足。请等待限流窗口恢复，或在供应商控制台核对当前模型的速率配额；本次未自动重试。"
    elif status == 400:
        code, message = "provider_invalid_request", "模型服务不接受当前请求，请检查模型、上下文长度及工具调用兼容性。"
    extra: dict[str, Any] = {"error_code": code, "upstream_status": status}
    if retry_after.isdigit() and 0 < int(retry_after) <= 86400:
        extra["retry_after_seconds"] = int(retry_after)
        message += f"供应商建议 {int(retry_after)} 秒后再试。"
    return StreamEvent(type="error", content=message, extra=extra)


class OpenAIProvider(AIProviderBase):
    """OpenAI 兼容接口实现"""

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式聊天"""
        body = self.build_request(messages, tools)
        url = resolve_endpoint(self.base_url, "/chat/completions")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        received = False
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code != 200:
                        raw = await resp.aread()
                        try:
                            payload = json.loads(raw)
                        except (ValueError, UnicodeDecodeError):
                            payload = {}
                        yield provider_error(payload, resp.status_code, resp.headers.get("Retry-After", ""))
                        return
                    async for event in self.parse_stream(resp):
                        received = True
                        yield event
                        if event.type == "error":
                            return
        except httpx.TimeoutException:
            yield StreamEvent(type="error", content="模型服务响应超时，本次未自动重试，请稍后再试。", extra={"error_code": "provider_timeout"})
            return
        except httpx.RequestError:
            yield StreamEvent(type="error", content="无法连接模型服务，请检查渠道地址或网络；本次未自动重试。", extra={"error_code": "provider_connection_failed"})
            return
        if not received:
            yield StreamEvent(type="error", content="模型服务返回空响应，未生成回答，请检查模型及流式接口兼容性。", extra={"error_code": "provider_empty_response"})
            return

        if getattr(self, "finish_reason", None) == "length":
            yield StreamEvent(type="error", content="回复达到本次输出上限，内容可能未完整；已收到的内容保留，可缩短问题后手动继续。", extra={"error_code": "provider_output_truncated"})
            return
        if not getattr(self, "stream_complete", False):
            yield StreamEvent(type="error", content="模型响应未正常结束，部分内容保留；未自动重试。", extra={"error_code": "provider_incomplete_response"})
            return

        yield StreamEvent(type="done")

    async def list_models(self) -> list[str]:
        """获取模型列表"""
        url = resolve_endpoint(self.base_url, "/models")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return [m["id"] for m in data.get("data", [])]

    def build_request(
        self,
        messages: list[dict],
        tools: list[ToolDef] | None = None,
    ) -> dict:
        """构造 OpenAI 格式请求体"""
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": getattr(self, "output_token_limit", 4096),
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        return body

    async def parse_stream(self, response: Any) -> AsyncIterator[StreamEvent]:
        """解析 OpenAI SSE 流 —— 文本/思考增量；工具调用尽早通知前端"""
        tool_calls_buffer: dict[int, dict[str, Any]] = {}
        self.stream_complete = False
        self.finish_reason = None

        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                self.stream_complete = True
                for tool_call in tool_calls_buffer.values():
                    yield StreamEvent(
                        type="tool_call",
                        tool_name=tool_call.get("function", {}).get("name", ""),
                        tool_args=tool_call.get("function", {}).get("arguments", ""),
                        tool_call_id=tool_call.get("id", ""),
                    )
                return

            try:
                chunk = json.loads(data)
            except ValueError:
                yield StreamEvent(type="error", content="模型服务流式响应解析失败，请检查渠道协议。", extra={"error_code": "provider_invalid_stream"})
                return
            if not isinstance(chunk, dict):
                yield provider_error({})
                return
            if chunk.get("error"):
                yield provider_error(chunk)
                return
            if isinstance(chunk.get("usage"), dict):
                self.last_usage = {key: value for key, value in chunk["usage"].items() if key in ("prompt_tokens", "completion_tokens", "total_tokens") and type(value) is int and 0 <= value <= 1000000000}
            choices = chunk.get("choices")
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str):
                self.finish_reason = finish_reason
                if finish_reason in ("stop", "tool_calls", "function_call"):
                    self.stream_complete = True

            if delta.get("reasoning_content"):
                yield StreamEvent(type="thinking", content=delta["reasoning_content"])

            if delta.get("content"):
                yield StreamEvent(type="message", content=delta["content"])

            if delta.get("tool_calls"):
                for tool_delta in delta["tool_calls"]:
                    index = tool_delta.get("index", 0)
                    if index not in tool_calls_buffer:
                        tool_calls_buffer[index] = {
                            "id": tool_delta.get("id", ""),
                            "function": {"name": "", "arguments": ""},
                            "started": False,
                        }
                    entry = tool_calls_buffer[index]
                    if tool_delta.get("id"):
                        entry["id"] = tool_delta["id"]
                    function_delta = tool_delta.get("function", {})
                    if function_delta.get("name"):
                        entry["function"]["name"] = function_delta["name"]
                    if function_delta.get("arguments"):
                        entry["function"]["arguments"] += function_delta["arguments"]

                    # 首次拿到工具名就推送，前端立刻显示「正在调用」
                    if entry["function"]["name"] and not entry["started"]:
                        entry["started"] = True
                        yield StreamEvent(
                            type="tool_call",
                            tool_name=entry["function"]["name"],
                            tool_args="",
                            tool_call_id=entry.get("id", ""),
                        )

            # finish_reason=tool_calls 时立刻刷出完整参数（不必等 [DONE]）
            if finish_reason == "tool_calls" and tool_calls_buffer:
                for tool_call in tool_calls_buffer.values():
                    yield StreamEvent(
                        type="tool_call",
                        tool_name=tool_call.get("function", {}).get("name", ""),
                        tool_args=tool_call.get("function", {}).get("arguments", ""),
                        tool_call_id=tool_call.get("id", ""),
                    )
                tool_calls_buffer.clear()
