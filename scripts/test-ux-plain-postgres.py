import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
import httpx
import anyio
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.api import ai_chat
from app.core.deps import AuthContext, get_auth_context
from app.models.ai_provider import AIProvider
from app.models.ai_model import AIModel
from app.models.ai_conversation import AIConversation, AIMessage
from app.services.ai import gateway
from app.services.ai.plain_chat import claim_request, finish_request, plain_stream, prepare_plain_input, read_request
from app.services.ai.providers.base import StreamEvent
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings

specification = importlib.util.spec_from_file_location("plain_migration", ROOT / "backend/scripts/migrate_plain_chat.py")
migration = importlib.util.module_from_spec(specification)
specification.loader.exec_module(migration)


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated local PG only")
    engine = create_replay_engine(settings)
    namespace = "qa_plain_" + uuid4().hex
    private = create_async_engine(engine.url, echo=False, connect_args={"server_settings": {"search_path": namespace}, "timeout": 10, "command_timeout": 15})
    factory = async_sessionmaker(private, expire_on_commit=False)
    checks = []
    report = {"status": "running", "checks": checks, "public_business_writes": 0, "source_calls": 0, "paid_model_calls": 0,
              "scope": "real_private_PG_and_ASGI_resource_permissions_with_fixture_auth_and_provider_not_real_JWT_or_paid_provider"}

    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value:
            raise AssertionError(name)

    async def public_hash():
        async with engine.connect() as connection:
            return await connection.scalar(text("SELECT md5(coalesce(string_agg(md5(to_jsonb(t)::text),'' ORDER BY id),'')) FROM public.ai_trading_tasks t"))

    before = await public_hash()
    owner, other, model_id, provider_id, conversation = [uuid4() for _ in range(5)]
    calls = []
    behavior = {"error": False, "hang": False, "active": 0, "closed": False}

    class Provider:
        last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        async def chat_stream(self, messages, tools):
            check("one_text_call_no_tools_256", not tools and self.output_token_limit == 256)
            calls.append(messages)
            if behavior["hang"]:
                behavior["active"] += 1
                try:
                    yield StreamEvent(type="message", content="取消测试")
                    await anyio.sleep_forever()
                finally:
                    behavior["active"] -= 1
                    behavior["closed"] = True
                return
            if behavior["error"]:
                yield StreamEvent(type="error", content="fixture限流", extra={"error_code": "provider_rate_limited", "retry_after_seconds": 12, "upstream_status": 429})
            else:
                yield StreamEvent(type="message", content="仅隔离测试文本")
                yield StreamEvent(type="done")

    try:
        async with asyncio.timeout(70):
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            try:
                async with private.begin() as connection:
                    await migration.migrate(connection)
            except RuntimeError:
                check("migration_check_before_apply_refuses", True)
            async with private.begin() as connection:
                await migration.migrate(connection, apply=True)
                check("repeat_migration_idempotent", not (await migration.migrate(connection, apply=True))["applied"])
                for model in (AIProvider, AIModel, AIConversation, AIMessage):
                    await connection.run_sync(lambda sync, model=model: model.__table__.create(sync))
                await connection.execute(insert(AIProvider).values(id=provider_id, user_id=owner, name="fixture", api_type="openai", base_url="https://unused.invalid/v1", api_key="synthetic", is_active=True))
                await connection.execute(insert(AIModel).values(id=model_id, user_id=owner, provider_id=provider_id, model_id="fixture", display_name="fixture", is_multimodal=False, is_custom=True))
                await connection.execute(insert(AIConversation).values(id=conversation, user_id=owner, title="fixture", model_id="fixture"))
            application = FastAPI()
            application.include_router(ai_chat.router)
            application.state.session_factory = factory
            application.state.redis = None

            async def identity(request: Request):
                kind = request.headers.get("X-Fixture-Identity")
                if kind is None:
                    raise HTTPException(401)
                return AuthContext(user=SimpleNamespace(id=other if kind == "other" else owner, market_type="stock" if kind == "stock" else "futures"), trading_mode="live" if kind == "other_mode" else "virtual")

            application.dependency_overrides[get_auth_context] = identity
            payload = {"model_id": "fixture", "model_record_id": str(model_id), "conversation_id": str(conversation), "chat_mode": "plain",
                       "client_request_id": str(uuid4()), "messages": [{"role": "user", "content": "你好"}]}
            async with AsyncExitStack() as stack:
                client = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://private", timeout=20))
                stack.enter_context(patch.object(gateway, "decrypt", return_value="fixture"))
                stack.enter_context(patch.object(gateway, "create_provider", return_value=Provider()))
                enrich = stack.enter_context(patch.object(ai_chat, "_build_enriched_context"))
                check("anonymous_chat_401", (await client.post("/api/ai/chat", json=payload)).status_code == 401)
                headers = {"X-Fixture-Identity": "owner"}
                response = await client.post("/api/ai/chat", json=payload, headers=headers)
                check("normal_text_SSE_and_durable_terminal", response.status_code == 200 and "仅隔离测试文本" in response.text and "event: done" in response.text and "event: error" not in response.text)
                check("no_RAG_memory_or_summary_path", enrich.call_count == 0 and len(calls) == 1)
                duplicate = await client.post("/api/ai/chat", json=payload, headers=headers)
                check("duplicate_never_rebills", duplicate.status_code == 409 and len(calls) == 1)
                status_path = "/api/ai/chat/requests/" + payload["client_request_id"]
                stored = await client.get(status_path, headers=headers)
                check("own_status_usage_cost_unknown", stored.status_code == 200 and stored.json()["status"] == "completed" and stored.json()["usage_json"]["total_tokens"] == 15 and stored.json()["cost_state"] == "unknown")
                for identity_kind, expected in (("other", 404), ("other_mode", 404), ("stock", 409)):
                    check("resource_scope_" + identity_kind, (await client.get(status_path, headers={"X-Fixture-Identity": identity_kind})).status_code == expected)
                check("anonymous_resource_401", (await client.get(status_path)).status_code == 401)
                payload["client_request_id"] = str(uuid4())
                behavior["error"] = True
                failed = await client.post("/api/ai/chat", json=payload, headers=headers)
                check("provider_429_metadata_preserved_no_retry", '"provider_rate_limited"' in failed.text and '"retry_after_seconds": 12' in failed.text and len(calls) == 2)
                failed_state = (await client.get("/api/ai/chat/requests/" + payload["client_request_id"], headers=headers)).json()
                check("provider_failure_persisted_not_completed", failed_state["status"] == "failed" and failed_state["cost_state"] == "unknown")
            parameters = prepare_plain_input("fixture", model_id, None, [{"role": "user", "content": "fixture"}], "virtual")
            request_id = uuid4()
            async with factory() as session, session.begin():
                await claim_request(session, request_id, owner, "virtual", parameters)
            async def compete():
                try:
                    async with factory() as session, session.begin():
                        await claim_request(session, uuid4(), owner, "virtual", parameters)
                    return True
                except ValueError:
                    return False
            check("concurrent_user_guard_before_any_model", not any(await asyncio.gather(*[compete() for _ in range(4)])))
            async with private.begin() as connection:
                await connection.execute(text("UPDATE plain_chat_requests SET deadline_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=:id"), {"id": request_id})
            async with factory() as session, session.begin():
                expired = await read_request(session, request_id, owner, "virtual")
                check("GET_expiry_reconciles_without_new_POST", expired["status"] == "unknown" and expired["error_code"] == "execution_expired")
            replacement = uuid4()
            async with factory() as session, session.begin():
                await claim_request(session, replacement, owner, "virtual", parameters)
            async with private.connect() as connection:
                check("expired_becomes_unknown_not_reexecuted", await connection.scalar(text("SELECT status FROM plain_chat_requests WHERE id=:id"), {"id": request_id}) == "unknown")
                check("user_messages_only_once_for_success_request", await connection.scalar(text("SELECT count(*) FROM ai_messages WHERE conversation_id=:id AND role='assistant'"), {"id": conversation}) == 1)
            await finish_request(factory, replacement, owner, "unknown", "", {}, "test_cancelled")
            cancelled = uuid4()
            async with factory() as session, session.begin():
                await claim_request(session, cancelled, owner, "virtual", parameters)
            stream = plain_stream(factory, owner, "fixture", model_id, None, cancelled, parameters,
                SimpleNamespace(user_id=owner, trading_mode="virtual", market_type="futures", request_id=cancelled))
            check("initial_status_before_provider", "event: status" in await anext(stream))
            await stream.aclose()
            async with factory() as session:
                cancelled_row = await read_request(session, cancelled, owner, "virtual")
                check("close_after_ack_marks_unknown_not_running", cancelled_row["status"] == "unknown")
            cancelled_during_output = uuid4()
            async with factory() as session, session.begin():
                await claim_request(session, cancelled_during_output, owner, "virtual", parameters)
            behavior.update(hang=True, error=False)
            with patch.object(gateway, "decrypt", return_value="fixture"), patch.object(gateway, "create_provider", return_value=Provider()):
                async with factory() as session:
                    direct = gateway.chat_stream(session, str(owner), "fixture", parameters["messages"], tools_enabled=False,
                        model_record_id=model_id, max_output_tokens=256)
                    await anext(direct)
                    await anext(direct)
                    await direct.aclose()
                    check("gateway_aclose_owns_provider_lifetime", behavior["closed"] and behavior["active"] == 0)
                behavior["closed"] = False
                with anyio.CancelScope() as scope:
                    from contextlib import aclosing
                    async with aclosing(plain_stream(factory, owner, "fixture", model_id, None, cancelled_during_output, parameters,
                            SimpleNamespace(user_id=owner, trading_mode="virtual", market_type="futures", request_id=cancelled_during_output))) as stream:
                        async for output in stream:
                            if "event: message" in output:
                                scope.cancel()
                                await anyio.sleep(0)
            check("persistent_cancellation_closes_provider_immediately", behavior["closed"] and behavior["active"] == 0)
            async with factory() as session:
                cancelled_row = await read_request(session, cancelled_during_output, owner, "virtual")
                check("persistent_cancellation_saves_unknown", cancelled_row["status"] == "unknown")
            check("public_AI_tasks_unchanged", await public_hash() == before)
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await private.dispose()
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
        await engine.dispose()
        report["private_schema_cleaned"] = True
        (ROOT / "logs/ux-plain-private-pg.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps(report, ensure_ascii=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
