import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
from uuid import UUID, uuid4

from sqlalchemy import insert, text


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.ai_conversation import router
from app.models.ai_conversation import AIConversation, AIMessage


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    application.include_router(router)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    conversation_id = UUID(int=1000)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Private schema required")
        public_counts = tuple([await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in ("ai_conversations", "ai_messages")])
        for table in ("ai_conversations", "ai_messages"):
            await session.execute(text(f'CREATE TABLE "{namespace}".{table} (LIKE public.{table} INCLUDING ALL)'))
        await session.execute(text(f'ALTER TABLE "{namespace}".ai_messages ADD CONSTRAINT b13_private_conversation_fk FOREIGN KEY (conversation_id) REFERENCES "{namespace}".ai_conversations(id) ON DELETE CASCADE'))
        conversations = [{"id": UUID(int=1000 + index), "user_id": owner_id, "title": f"Private B13 fixture {index}", "model_id": "fixture", "created_at": stamp, "updated_at": stamp} for index in range(51)]
        conversations.extend({"id": uuid4(), "user_id": identity, "title": "Foreign B13 fixture", "model_id": "fixture", "created_at": stamp, "updated_at": stamp} for identity in (other_id, stock_id))
        await session.execute(insert(AIConversation), conversations)
        await session.execute(insert(AIMessage), [{"id": UUID(int=index + 1), "conversation_id": conversation_id, "role": "user" if index % 2 == 0 else "assistant", "content": f"Synthetic message {index + 1}", "created_at": stamp,
            "tool_calls": [{"name": "paper_get_account", "args": "{}", "call_id": "fixture"}] if index == 1 else None,
            "tool_result": [{"name": "paper_get_account", "content": "fixture-result", "call_id": "fixture"}] if index == 1 else None} for index in range(75)])
        await session.commit()

    base = "/api/ai/conversations"
    check("b13_history_requires_real_JWT", (await client.get(base)).status_code == 401)
    collected = []
    for offset in (0, 20, 40):
        response = await client.get(f"{base}?limit=20&offset={offset}", headers=headers(owner_id))
        check("b13_bounded_real_PG_list_with_no_store_trace", response.status_code == 200 and len(response.json()) == min(20, 51 - offset) and response.headers.get("X-Request-ID") and response.headers.get("Cache-Control") == "no-store")
        collected.extend(item["id"] for item in response.json())
    check("b13_tied_timestamps_stable_owned_list_unique_union", collected == [str(UUID(int=index)) for index in range(1050, 999, -1)])
    check("b13_legacy_array_contract_default_50", len((await client.get(base, headers=headers(owner_id))).json()) == 50)
    for suffix in ("limit=0", "limit=101", "offset=-1", "offset=10001"):
        response = await client.get(base + "?" + suffix, headers=headers(owner_id))
        check("b13_invalid_bounds_422_with_trace", response.status_code == 422 and bool(response.headers.get("X-Request-ID")))
    pages = []
    for page, expected_count in ((1, 50), (2, 25), (3, 0)):
        response = await client.get(f"{base}/{conversation_id}/messages?page={page}&size=50", headers=headers(owner_id))
        payload = response.json()
        check("b13_last_message_window_exact_and_bounded", response.status_code == 200 and payload["total"] == 75 and payload["page"] == page and payload["size"] == 50 and len(payload["items"]) == expected_count)
        pages.append([item["id"] for item in payload["items"]])
        if page == 2:
            check("b13_actual_tool_arrays_survive_real_PG_HTTP_schema", payload["items"][1]["tool_calls"][0]["call_id"] == "fixture" and payload["items"][1]["tool_result"][0]["content"] == "fixture-result")
    check("b13_real_PG_message_pages_union_no_overlap_with_ties", pages[0] == [str(UUID(int=index)) for index in range(26, 76)] and pages[1] == [str(UUID(int=index)) for index in range(1, 26)] and not set(pages[0]).intersection(pages[1]))
    for identity in (other_id, stock_id):
        check("b13_foreign_message_read_404", (await client.get(f"{base}/{conversation_id}/messages", headers=headers(identity))).status_code == 404)
        check("b13_foreign_delete_404", (await client.delete(f"{base}/{conversation_id}", headers=headers(identity))).status_code == 404)
    created = await client.post(base, json={"model_id": "fixture"}, headers=headers(owner_id))
    check("b13_real_PG_create_compatible", created.status_code == 200 and created.json()["model_id"] == "fixture")
    new_id = UUID(created.json()["id"])
    async with scoped() as session:
        session.add(AIMessage(id=uuid4(), conversation_id=new_id, role="user", content="Synthetic cascade fixture", created_at=stamp))
        await session.commit()
    check("b13_real_PG_owned_delete", (await client.delete(f"{base}/{new_id}", headers=headers(owner_id))).status_code == 200)
    async with scoped() as session:
        count = await session.scalar(text("SELECT count(*) FROM ai_messages WHERE conversation_id=:identifier"), {"identifier": new_id})
        check("b13_real_PG_private_cascade_no_orphan", count == 0)
        check("b13_public_conversations_messages_not_written", tuple([await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in ("ai_conversations", "ai_messages")]) == public_counts)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b13"))
