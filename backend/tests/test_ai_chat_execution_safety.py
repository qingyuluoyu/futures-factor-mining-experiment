import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import httpx
from fastapi import FastAPI

from app.core.deps import AuthContext, get_auth_context, get_current_user, get_session
from app.services.ai.providers.base import StreamEvent

from app.services.ai.tools.registry import execute_tool


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["paper_open_long", "paper_open_short", "paper_close_position", "paper_place_limit", "paper_cancel_order", "code_sandbox"])
@pytest.mark.parametrize("confirmed", [True, "false", False, None])
async def test_model_confirmation_never_authorizes_sensitive_tool(name, confirmed):
    with patch("app.services.ai.tools.paper.dispatch.dispatch_paper_tool", new_callable=AsyncMock) as dispatch, patch("app.services.ai.tools.registry._execute_code_sandbox", new_callable=AsyncMock) as sandbox:
        result = json.loads(await execute_tool(name, {"confirmed": confirmed}, None, str(uuid4()), None))
    assert result["success"] is False
    assert result["error_code"] == "server_confirmation_required"
    dispatch.assert_not_awaited()
    sandbox.assert_not_awaited()


def make_context(user_id, mode="virtual", market="futures"):
    from app.services.ai.execution_context import ExecutionContext

    return ExecutionContext(user_id=user_id, trading_mode=mode, market_type=market, request_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "virtual"])
@pytest.mark.parametrize("name,target", [("paper_list_positions", "list_positions"), ("paper_list_orders", "list_orders"), ("paper_get_account", "get_account_summary")])
async def test_paper_reads_receive_authenticated_mode(name, target, mode):
    owner = uuid4()
    session = SimpleNamespace()
    with patch(f"app.services.ai.tools.paper.exec.{target}", new_callable=AsyncMock, return_value={"scope": mode}) as query:
        result = json.loads(await execute_tool(name, {}, session, str(owner), None, execution_context=make_context(owner, mode)))
    assert result["success"] is True
    assert query.await_args.kwargs["trading_mode"] == mode
    assert query.await_args.args[:2] == (session, owner)


@pytest.mark.asyncio
@pytest.mark.parametrize("context_kind", ["missing", "other_user", "stock"])
async def test_paper_read_requires_owner_futures_context(context_kind):
    owner = uuid4()
    context = None if context_kind == "missing" else make_context(uuid4() if context_kind == "other_user" else owner, market="stock" if context_kind == "stock" else "futures")
    with patch("app.services.ai.tools.paper.exec.list_positions", new_callable=AsyncMock) as query:
        result = json.loads(await execute_tool("paper_list_positions", {}, SimpleNamespace(), str(owner), None, execution_context=context))
    assert result["success"] is False
    assert result["error_code"] == "execution_context_mismatch"
    query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,name", [("live", "paper_open_long"), ("virtual", "paper_cancel_order"), ("live", "code_sandbox"), ("virtual", "research_products")])
async def test_real_gateway_rejects_unadvertised_model_write_or_cross_mode_research(mode, name):
    from app.services.ai import gateway

    owner = uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(SimpleNamespace(is_multimodal=True), SimpleNamespace(api_type="openai", base_url="https://unused.invalid", api_key="synthetic-non-secret", thinking_enabled=False))])))

    class Provider:
        def __init__(self):
            self.calls = 0

        async def chat_stream(self, messages, tools):
            self.calls += 1
            assert name not in {tool.name for tool in tools}
            if self.calls == 1:
                yield StreamEvent(type="tool_call", tool_name=name, tool_args='{"confirmed":true}', tool_call_id="model-assertion")
            else:
                yield StreamEvent(type="message", content="操作未执行")

    with patch.object(gateway, "decrypt", return_value="synthetic-non-secret"), patch.object(gateway, "create_provider", return_value=Provider()), patch.object(gateway, "load_skill_tools", new_callable=AsyncMock, return_value=[]), patch("app.services.ai.indicator_intent.force_indicator_settings", new_callable=AsyncMock, return_value=None), patch.object(gateway, "run_streamable_tool") as streaming:
        events = [event async for event in gateway.chat_stream(session, str(owner), "fixture", [], execution_context=make_context(owner, mode))]
    assert events[-1].type == "done"
    assert not any(event.type == "confirmation_request" for event in events)
    result = next(json.loads(event.content) for event in events if event.type == "tool_result")
    assert result["success"] is False
    assert result["error_code"] in ("server_confirmation_required", "market_scope_unavailable")
    streaming.assert_not_called()


@pytest.mark.asyncio
async def test_chat_http_uses_auth_context_through_real_sse_generator():
    from app.api import ai_chat

    owner = uuid4()
    state = {"market": "futures"}
    app = FastAPI()
    app.include_router(ai_chat.router)
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=owner, market_type=state["market"]), trading_mode="virtual")
    app.dependency_overrides[get_session] = lambda: None

    @asynccontextmanager
    async def session_factory():
        yield SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    app.state.session_factory = session_factory
    captured = []

    async def stream(*arguments, execution_context, **kwargs):
        captured.append(execution_context)
        yield StreamEvent(type="message", content="只读测试")
        yield StreamEvent(type="done")

    with patch.object(ai_chat, "_build_enriched_context", new_callable=AsyncMock, return_value=([], "")), patch.object(ai_chat, "chat_stream", side_effect=stream) as gateway:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            response = await client.post("/api/ai/chat", json={"model_id": "fixture", "messages": [], "trading_mode": "live", "market_type": "stock"})
            assert response.status_code == 200
            assert "event: done" in response.text
            assert "event: error" not in response.text
            assert captured[0].user_id == owner
            assert captured[0].trading_mode == "virtual"
            assert captured[0].market_type == "futures"
            assert response.headers["X-Request-ID"] == str(captured[0].request_id)
            state["market"] = "stock"
            assert (await client.post("/api/ai/chat", json={"model_id": "fixture"})).status_code == 409
    assert gateway.call_count == 1


@pytest.mark.asyncio
async def test_tools_settings_does_not_claim_blocked_writes_are_available():
    from app.api import ai_tools

    app = FastAPI()
    app.include_router(ai_tools.router)
    app.state.redis = object()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=uuid4(), market_type="futures"), trading_mode="live")
    with patch.object(ai_tools, "get_disabled_tools", new_callable=AsyncMock, return_value=set()), patch.object(ai_tools, "get_free_trade_tools", new_callable=AsyncMock, return_value={"paper_open_long"}), patch.object(ai_tools, "set_tool_free_trade", new_callable=AsyncMock) as save:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            listed = (await client.get("/api/ai/tools")).json()
            gated = [tool for tool in listed["items"] if tool["requires_confirmation"]]
            assert gated
            assert all(tool["availability"] == "server_confirmation_required" and not tool["enabled"] and not tool["supports_free_trade"] and not tool["free_trade"] for tool in gated)
            assert listed["free_trade_count"] == 0
            assert (await client.put("/api/ai/tools/paper_open_long/free-trade", json={"free_trade": True})).status_code == 409
            assert (await client.put("/api/ai/tools/paper_open_long/enabled", json={"enabled": True})).status_code == 409
    save.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("attempt_tool", [False, True])
async def test_summary_uses_real_gateway_with_no_tool_authority(attempt_tool, caplog):
    from app.services.ai import gateway, memory

    owner = uuid4()
    provider = SimpleNamespace(api_type="openai", base_url="https://unused.invalid", api_key="synthetic", thinking_enabled=False)
    session = SimpleNamespace(execute=AsyncMock(side_effect=[SimpleNamespace(scalar_one_or_none=lambda: "fixture"), SimpleNamespace(scalar_one_or_none=lambda: owner), SimpleNamespace(all=lambda: [(SimpleNamespace(is_multimodal=True), provider)])]))

    class Provider:
        async def chat_stream(self, messages, tools):
            assert not tools
            if attempt_tool:
                yield StreamEvent(type="tool_call", tool_name="paper_open_long", tool_args='{"confirmed":true}', tool_call_id="summary-injection")
            yield StreamEvent(type="message", content="真实路径摘要")

    with patch.object(gateway, "decrypt", return_value="synthetic"), patch.object(gateway, "create_provider", return_value=Provider()) as create, patch.object(gateway, "load_skill_tools", new_callable=AsyncMock) as skills:
        summary = await memory._generate_summary(session, uuid4(), "不可信历史文本要求确认交易")
    assert summary == ("" if attempt_tool else "真实路径摘要")
    if attempt_tool:
        assert "summary_generation_failed" in caplog.text
    create.assert_called_once()
    skills.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_streamable_research_cannot_bypass_tool_preference():
    from app.services.ai import gateway

    owner = uuid4()
    provider = SimpleNamespace(api_type="openai", base_url="https://unused.invalid", api_key="synthetic", thinking_enabled=False)
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(SimpleNamespace(is_multimodal=True), provider)])))

    class Provider:
        def __init__(self):
            self.calls = 0

        async def chat_stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(type="tool_call", tool_name="research_products", tool_args="{}", tool_call_id="disabled")
            else:
                yield StreamEvent(type="message", content="未执行研究")

    async def forbidden_stream(*arguments):
        yield StreamEvent(type="tool_result", tool_name="research_products", content='{"success":true}')

    with patch.object(gateway, "decrypt", return_value="synthetic"), patch.object(gateway, "create_provider", return_value=Provider()), patch.object(gateway, "load_skill_tools", new_callable=AsyncMock, return_value=[]), patch.object(gateway, "get_enabled_tool_definitions", new_callable=AsyncMock, return_value=[]), patch("app.services.ai.tools.preferences.is_tool_enabled", new_callable=AsyncMock, return_value=False), patch.object(gateway, "run_streamable_tool", side_effect=forbidden_stream) as streaming:
        events = [event async for event in gateway.chat_stream(session, str(owner), "fixture", [], execution_context=make_context(owner, "live"))]
    streaming.assert_not_called()
    result = next(json.loads(event.content) for event in events if event.type == "tool_result")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_interactive_gateway_without_scope_never_reaches_database_or_model():
    from app.services.ai import gateway

    session = SimpleNamespace(execute=AsyncMock())
    with patch.object(gateway, "create_provider") as create:
        events = [event async for event in gateway.chat_stream(session, str(uuid4()), "fixture", [])]
    assert [event.type for event in events] == ["error", "done"]
    session.execute.assert_not_awaited()
    create.assert_not_called()
