"""Claude 规范 Provider —— Anthropic 专用接口"""

import json
from typing import AsyncIterator

import httpx

from app.services.ai.providers.base import (
    AIProviderBase,
    StreamEvent,
    ToolDef,
)
from app.services.ai.providers.urls import resolve_endpoint
from app.services.ai.providers.openai_provider import provider_error


class ClaudeProvider(AIProviderBase):
    """Anthropic Claude 接口实现"""

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[ToolDef] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式聊天"""
        body = self.build_request(messages, tools)
        url = resolve_endpoint(self.base_url, "/messages")
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

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
                    yield event
                    if event.type == "error":
                        return

        if getattr(self, "finish_reason", None) == "max_tokens":
            yield StreamEvent(type="error", content="回复达到输出上限，内容可能未完整；已收到内容保留。", extra={"error_code": "provider_output_truncated"})
            return
        if not getattr(self, "stream_complete", False):
            yield StreamEvent(type="error", content="模型响应未正常结束，部分内容保留；未自动重试。", extra={"error_code": "provider_incomplete_response"})
            return

        yield StreamEvent(type="done")

    async def list_models(self) -> list[str]:
        """获取模型列表"""
        url = resolve_endpoint(self.base_url, "/models")
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
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
        """构造 Claude 格式请求体"""
        # Claude 需要 system 消息单独提取
        system_text = ""
        chat_messages: list[dict] = []
        for msg in messages:
            if msg["role"] == "system":
                system_text += msg.get("content", "") + "\n"
            else:
                chat_messages.append(msg)

        body: dict = {
            "model": self.model_id,
            "messages": chat_messages,
            "max_tokens": getattr(self, "output_token_limit", 16384),
            "stream": True,
        }
        if system_text.strip():
            body["system"] = system_text.strip()

        # 工具定义
        if tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        # 思考模式：渠道级开关优先；未配置时保持原自动判定（模型名/白名单）。
        # 强制关闭=不发 thinking 参数（Anthropic 与 MiniMax M3 默认即关）。
        want_thinking: bool = (
            self.thinking_enabled
            if self.thinking_enabled is not None
            else ("thinking" in self.model_id or self._supports_thinking())
        )
        if want_thinking:
            if self._is_minimax():
                # MiniMax 兼容层仅支持 adaptive 开启（enabled/budget_tokens 不支持）
                body["thinking"] = {"type": "adaptive"}
            else:
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": 10000,
                }

        return body

    def _is_minimax(self) -> bool:
        """判定 MiniMax 渠道（官方域或 MiniMax- 前缀模型）"""
        base = self.base_url.lower()
        return (
            "minimax.cn" in base
            or "minimaxi.com" in base
            or self.model_id.lower().startswith("minimax")
        )

    def _supports_thinking(self) -> bool:
        """判断模型是否支持 extended thinking"""
        thinking_models = {"claude-sonnet-4", "claude-opus-4", "claude-3-7-sonnet"}
        return any(m in self.model_id for m in thinking_models)

    async def parse_stream(self, response) -> AsyncIterator[StreamEvent]:
        """解析 Claude SSE 流"""
        self.stream_complete = False
        self.finish_reason = None
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue

            try:
                chunk = json.loads(data)
                if not isinstance(chunk, dict):
                    raise ValueError("invalid event")
            except ValueError:
                yield StreamEvent(type="error", content="模型流式响应格式无效。", extra={"error_code": "provider_invalid_stream"})
                return
            event_type = chunk.get("type", "")
            if event_type == "message_stop":
                self.stream_complete = True
            elif event_type == "message_delta":
                self.finish_reason = chunk.get("delta", {}).get("stop_reason")

            if event_type == "content_block_start":
                block = chunk.get("content_block", {})
                if block.get("type") == "thinking":
                    # 思考内容在后续 delta 中
                    pass
                elif block.get("type") == "tool_use":
                    # 工具调用开始
                    yield StreamEvent(
                        type="tool_call",
                        tool_name=block.get("name", ""),
                        tool_args="",
                        tool_call_id=block.get("id", ""),
                    )

            elif event_type == "content_block_delta":
                delta = chunk.get("delta", {})
                delta_type = delta.get("type", "")

                if delta_type == "thinking_delta":
                    yield StreamEvent(
                        type="thinking",
                        content=delta.get("thinking", ""),
                    )
                elif delta_type == "text_delta":
                    yield StreamEvent(
                        type="message",
                        content=delta.get("text", ""),
                    )
                elif delta_type == "input_json_delta":
                    yield StreamEvent(
                        type="tool_call",
                        tool_args=delta.get("partial_json", ""),
                    )

            elif event_type == "error":
                yield provider_error(chunk)
                return
