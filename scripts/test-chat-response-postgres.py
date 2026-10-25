import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import select, text


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.models.ai_conversation import AIConversation, AIMessage
from app.services.ai import gateway
from app.services.ai.providers.openai_provider import OpenAIProvider


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, **kwargs):
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        for table in ("ai_conversations", "ai_messages"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        conversation = AIConversation(user_id=owner_id, title="isolated-chat", model_id="qa-b02")
        session.add(conversation)
        await session.flush()
        session.add(AIMessage(conversation_id=conversation.id, role="user", content="旧的失败问题"))
        await session.commit()
        conversation_id = conversation.id

    requests = []
    state = {"limited": True}
    original_client = httpx.AsyncClient

    def reply(request):
        requests.append(json.loads(request.content))
        if state["limited"]:
            return httpx.Response(429, headers={"Retry-After": "12"}, json={"error": {"message": "upstream private body", "code": "RateLimitExceeded.EndpointTPMExceeded"}})
        chunks = [{"choices": [{"delta": {"content": "隔离链路连接成功"}}]}, {"choices": [], "usage": {"total_tokens": 12}}]
        content = "".join("data:" + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    from app.api import ai_chat

    with patch.object(gateway, "create_provider", return_value=OpenAIProvider("https://fixture.invalid/v1", "synthetic", "qa-b02")), patch.object(gateway, "get_enabled_tool_definitions", new_callable=AsyncMock, return_value=[]), patch("app.services.ai.tools.preferences.is_tool_enabled", new_callable=AsyncMock, return_value=False), patch.object(ai_chat, "retrieve_relevant_memories", new_callable=AsyncMock, return_value=[]), patch.object(ai_chat, "hybrid_search", new_callable=AsyncMock, return_value=[]), patch("app.services.ai.long_term_memory.store_conversation_summary", new_callable=AsyncMock), patch("app.services.ai.long_term_memory.extract_and_store_preferences", new_callable=AsyncMock), patch("app.services.ai.providers.openai_provider.httpx.AsyncClient", side_effect=lambda **parameters: original_client(transport=httpx.MockTransport(reply), **parameters)):
        body = {"model_id": "qa-b02", "conversation_id": str(conversation_id), "messages": [{"role": "user", "content": "最新限流问题"}]}
        limited = await client.post("/api/ai/chat", headers=headers(owner_id), json=body)
        check("http_rate_limit_single_error_and_done", limited.status_code == 200 and limited.text.count("event: error") == 1 and limited.text.count("event: done") == 1)
        check("http_rate_limit_code_and_retry_hint", "provider_rate_limited" in limited.text and "retry_after_seconds" in limited.text and "TPM/RPM" in limited.text)
        check("http_upstream_body_not_exposed", "upstream private body" not in limited.text)
        check("provider_called_once_without_retry", len(requests) == 1)
        check("latest_failed_prompt_reaches_provider", any(message.get("content") == "最新限流问题" for message in requests[-1]["messages"]))
        check("request_has_output_budget", requests[-1]["max_tokens"] == 4096)
        state["limited"] = False
        body["messages"][-1]["content"] = "失败后新的问题"
        success = await client.post("/api/ai/chat", headers=headers(owner_id), json=body)
        check("recovery_sse_has_real_text_and_single_done", success.status_code == 200 and "隔离链路连接成功" in success.text and success.text.count("event: done") == 1 and "event: error" not in success.text)
        user_messages = [message["content"] for message in requests[-1]["messages"] if message["role"] == "user"]
        check("recovery_uses_latest_prompt_exactly_once", user_messages[-1] == "失败后新的问题" and user_messages.count("失败后新的问题") == 1)
        before = len(requests)
        check("other_user_conversation_denied", (await client.post("/api/ai/chat", headers=headers(other_id), json=body)).status_code == 404)
        check("anonymous_chat_denied", (await client.post("/api/ai/chat", json=body)).status_code == 401)
        check("denied_requests_never_call_provider", before == len(requests))
    async with scoped() as session:
        rows = (await session.execute(select(AIMessage).where(AIMessage.conversation_id == conversation_id).order_by(AIMessage.created_at))).scalars().all()
        check("user_prompts_persisted_in_private_pg", [message.content for message in rows if message.role == "user"] == ["旧的失败问题", "最新限流问题", "失败后新的问题"])
        check("only_successful_answer_persisted_as_assistant", [message.content for message in rows if message.role == "assistant"] == ["隔离链路连接成功"])


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="chat"))
