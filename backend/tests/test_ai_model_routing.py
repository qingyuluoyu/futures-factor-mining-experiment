from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.api.ai_chat import ChatRequest
from app.services.ai import gateway
from app.services.ai.providers.base import StreamEvent


def test_chat_model_record_id_is_preserved_and_validated():
    identifier = uuid4()
    assert ChatRequest(model_id="fixture", model_record_id=str(identifier)).model_dump()["model_record_id"] == identifier
    with pytest.raises(ValueError):
        ChatRequest(model_id="fixture", model_record_id="not-a-uuid")


@pytest.mark.asyncio
@pytest.mark.parametrize("count,code", [(0, "model_unavailable"), (2, "model_ambiguous")])
async def test_unavailable_or_ambiguous_legacy_model_never_decrypts_or_calls_provider(count, code):
    row = (SimpleNamespace(is_multimodal=True), SimpleNamespace(api_key="synthetic"))
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(first=lambda: row if count else None, all=lambda: [row] * count)))
    with patch.object(gateway, "decrypt") as decrypt, patch.object(gateway, "create_provider") as create:
        events = [event async for event in gateway.chat_stream(session, str(uuid4()), "fixture", [], tools_enabled=False)]
    assert [event.type for event in events] == ["error", "done"]
    assert events[0].extra["error_code"] == code
    decrypt.assert_not_called()
    create.assert_not_called()


@pytest.mark.asyncio
async def test_exact_model_record_query_binds_both_owners_active_name_and_uuid():
    owner, identifier = uuid4(), uuid4()
    provider = SimpleNamespace(api_type="openai", base_url="https://unused.invalid", api_key="synthetic", thinking_enabled=False)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(SimpleNamespace(is_multimodal=True), provider)])))

    class Provider:
        async def chat_stream(self, messages, tools):
            assert not tools
            yield StreamEvent(type="message", content="fixture")

    with patch.object(gateway, "decrypt", return_value="synthetic"), patch.object(gateway, "create_provider", return_value=Provider()):
        events = [event async for event in gateway.chat_stream(session, str(owner), "fixture", [], tools_enabled=False, model_record_id=identifier)]
    assert any(event.content == "fixture" for event in events)
    query = session.execute.call_args.args[0]
    compiled = query.compile()
    assert query._limit_clause.value == 2
    assert "ai_providers.user_id" in str(query) and "ai_providers.is_active IS true" in str(query)
    assert identifier in compiled.params.values() and list(compiled.params.values()).count(owner) == 2
