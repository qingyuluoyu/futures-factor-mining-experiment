import json
from unittest.mock import patch

import httpx
import pytest

from app.services.ai.providers.openai_provider import OpenAIProvider


@pytest.mark.asyncio
async def test_rate_limit_is_safe_actionable_and_not_retried():
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "12"}, json={"error": {"message": "inference exceeds tpm/rpm limit secret-upstream", "code": "RateLimitExceeded.EndpointTPMExceeded"}})

    original = httpx.AsyncClient
    with patch("app.services.ai.providers.openai_provider.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs)):
        events = [event async for event in OpenAIProvider("https://provider.invalid/v1", "fixture", "fixture").chat_stream([{"role": "user", "content": "hi"}])]
    assert len(requests) == 1
    error = next(event for event in events if event.type == "error")
    assert error.extra["error_code"] == "provider_rate_limited"
    assert error.extra["retry_after_seconds"] == 12
    assert "TPM/RPM" in error.content and "密钥" in error.content
    assert "secret-upstream" not in error.content


class Lines:
    def __init__(self, chunks):
        self.chunks = chunks

    async def aiter_lines(self):
        for chunk in self.chunks:
            yield "data: " + json.dumps(chunk)


@pytest.mark.asyncio
async def test_streamed_error_is_not_silently_ignored():
    stream = Lines([{"error": {"message": "tpm/rpm limit", "code": "RateLimitExceeded.EndpointTPMExceeded"}}])
    events = [event async for event in OpenAIProvider("https://provider.invalid", "fixture", "fixture").parse_stream(stream)]
    assert len(events) == 1 and events[0].type == "error"
    assert events[0].extra["error_code"] == "provider_rate_limited"


@pytest.mark.asyncio
async def test_usage_only_and_compact_sse_are_accepted():
    stream = Lines([{"choices": [], "usage": {"total_tokens": 12}}, {"choices": [{"delta": {"content": "连接成功"}}]}])
    events = [event async for event in OpenAIProvider("https://provider.invalid", "fixture", "fixture").parse_stream(stream)]
    assert [event.content for event in events] == ["连接成功"]


def test_each_request_has_output_budget():
    body = OpenAIProvider("https://provider.invalid", "fixture", "fixture").build_request([])
    assert body["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_network_timeout_is_safe_and_not_retried():
    calls = []

    def reply(request):
        calls.append(request)
        raise httpx.ReadTimeout("private transport detail", request=request)

    original = httpx.AsyncClient
    with patch("app.services.ai.providers.openai_provider.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs)):
        events = [event async for event in OpenAIProvider("https://provider.invalid", "fixture", "fixture").chat_stream([])]
    assert len(calls) == 1 and len(events) == 1
    assert events[0].extra["error_code"] == "provider_timeout"
    assert "private transport" not in events[0].content


@pytest.mark.asyncio
async def test_new_prompt_after_failed_turn_is_not_lost():
    from app.api import ai_chat

    latest = {"role": "user", "content": [{"type": "text", "text": "新的问题"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}}]}
    with patch.object(ai_chat, "load_context_messages", return_value=[{"role": "user", "content": "上次失败的问题"}]), patch.object(ai_chat, "retrieve_relevant_memories", return_value=[]), patch.object(ai_chat, "hybrid_search", return_value=[]):
        messages, text = await ai_chat._build_enriched_context(None, "fixture", "fixture", [latest], None)
    assert text == "新的问题"
    assert messages[-1] == latest


@pytest.mark.asyncio
async def test_http_EOF_after_partial_answer_is_not_completed():
    original = httpx.AsyncClient
    reply = lambda request: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"部分"}}]}\n\n')
    with patch("app.services.ai.providers.openai_provider.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs)):
        events = [event async for event in OpenAIProvider("https://provider.invalid/v1", "fixture", "fixture").chat_stream([])]
    assert any(event.extra.get("error_code") == "provider_incomplete_response" for event in events)
    assert not any(event.type == "done" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason,terminal,expected", [
    (None, False, "provider_incomplete_response"),
    ("max_tokens", True, "provider_output_truncated"),
    ("end_turn", True, None),
])
async def test_claude_terminal_contract(stop_reason, terminal, expected):
    from app.services.ai.providers.claude_provider import ClaudeProvider
    chunks = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "fixture"}}]
    if stop_reason:
        chunks.append({"type": "message_delta", "delta": {"stop_reason": stop_reason}})
    if terminal:
        chunks.append({"type": "message_stop"})
    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    original = httpx.AsyncClient
    with patch("app.services.ai.providers.claude_provider.httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)), **kwargs)):
        events = [event async for event in ClaudeProvider("https://provider.invalid/v1", "fixture", "fixture").chat_stream([])]
    if expected:
        assert any(event.extra.get("error_code") == expected for event in events)
        assert not any(event.type == "done" for event in events)
    else:
        assert [event.type for event in events] == ["message", "done"]
