from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.ai_conversation import get_messages, list_conversations, router
from app.core.deps import get_current_user, get_session
from app.schemas.ai_conversation import MessageItem
from datetime import datetime, timezone


@pytest.mark.asyncio
@pytest.mark.parametrize("page,start,count", [(1, 25, 50), (2, 0, 25), (3, 0, 0)])
async def test_message_windows_never_overlap_at_short_last_page(page, start, count):
    session = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda: object()),
        SimpleNamespace(scalar_one=lambda: 75),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
    ]))
    result = await get_messages(uuid4(), page=page, size=50, session=session, current_user=SimpleNamespace(id=uuid4()))
    query = session.execute.call_args_list[-1].args[0]
    assert query._offset_clause.value == start
    assert query._limit_clause.value == count
    assert str(query).split("ORDER BY")[1].split("LIMIT")[0].strip() == "ai_messages.created_at ASC, ai_messages.id ASC"
    assert result.total == 75


@pytest.mark.asyncio
async def test_list_is_bounded_and_stably_ordered():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))))
    await list_conversations(limit=20, offset=40, session=session, current_user=SimpleNamespace(id=uuid4()))
    query = session.execute.call_args.args[0]
    assert query._limit_clause.value == 20
    assert query._offset_clause.value == 40
    assert "ai_conversations.updated_at DESC, ai_conversations.id DESC" in str(query)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["limit=0", "limit=101", "offset=-1", "offset=10001"])
async def test_invalid_list_page_is_rejected_before_query(suffix):
    application = FastAPI()
    application.include_router(router)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))))
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/ai/conversations?" + suffix)
    assert response.status_code == 422
    assert response.headers.get("X-Request-ID")
    session.execute.assert_not_called()


@pytest.mark.parametrize("payload", [[{"name": "paper_get_account", "args": "{}", "call_id": "fixture"}], {"legacy": "object"}])
def test_history_contract_accepts_actual_saved_tool_arrays_and_legacy_objects(payload):
    message = MessageItem.model_validate({"id": uuid4(), "conversation_id": uuid4(), "role": "assistant", "content": "fixture", "created_at": datetime.now(timezone.utc), "tool_calls": payload, "tool_result": payload})
    assert message.tool_calls == payload and message.tool_result == payload
