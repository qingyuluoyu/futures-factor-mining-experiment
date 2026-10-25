import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.services.ai import gateway
from app.services.ai.providers.base import StreamEvent


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    providers = [uuid4(), uuid4(), uuid4()]
    models = [uuid4(), uuid4(), uuid4(), uuid4()]
    async with scoped() as session:
        public_counts = [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in ("ai_models", "ai_providers")]
        session.add_all(AIProvider(id=identifier, user_id=owner_id if index < 2 else other_id, name=f"B14-synthetic-{index}", api_type="openai", base_url=f"https://unused-{index}.invalid", api_key="synthetic-non-secret", is_active=True) for index, identifier in enumerate(providers))
        await session.flush()
        session.add_all([
            AIModel(id=models[0], user_id=owner_id, provider_id=providers[0], model_id="b14-same-name", display_name="B14-A"),
            AIModel(id=models[1], user_id=owner_id, provider_id=providers[1], model_id="b14-same-name", display_name="B14-B"),
            AIModel(id=models[2], user_id=other_id, provider_id=providers[2], model_id="b14-same-name", display_name="B14-foreign"),
            AIModel(id=models[3], user_id=owner_id, provider_id=providers[2], model_id="b14-wrong-provider-owner", display_name="B14-inconsistent-private-fixture"),
        ])
        await session.commit()
    endpoint = "/api/ai/chat"
    capability = endpoint + "/capabilities"
    check("b14_capability_real_JWT_401", (await client.get(capability)).status_code == 401)
    check("b14_stock_agent_not_implicitly_opened", (await client.get(capability, headers=headers(stock_id))).status_code == 409)
    available = await client.get(capability, headers=headers(owner_id))
    check("b14_capability_explicit_trace_no_store", available.status_code == 200 and available.json() == {"model_routing": "owned-model-row-v1"} and available.headers.get("X-Request-ID") and available.headers.get("Cache-Control") == "no-store")
    captured = []

    class DirectProvider:
        async def chat_stream(self, messages, tools):
            yield StreamEvent(type="message", content="Synthetic selected provider response")

    def factory(api_type, base_url, api_key, model_id, **options):
        captured.append((base_url, model_id))
        return DirectProvider()

    async def denied(body, code, identity=owner_id):
        before = len(captured)
        response = await client.post(endpoint, headers=headers(identity), json=body)
        check("b14_denied_route_stable_error_and_done", response.status_code == 200 and f'"error_code": "{code}"' in response.text and "event: done" in response.text)
        check("b14_denied_route_no_provider_factory_or_charge", len(captured) == before)

    with patch.object(gateway, "create_provider", side_effect=factory):
        await denied({"model_id": "b14-same-name"}, "model_ambiguous")
        for index in (0, 1):
            response = await client.post(endpoint, headers=headers(owner_id), json={"model_id": "b14-same-name", "model_record_id": str(models[index])})
            check("b14_exact_owned_row_selected_in_real_PG_HTTP", response.status_code == 200 and "Synthetic selected provider response" in response.text and captured[-1] == (f"https://unused-{index}.invalid", "b14-same-name"))
        await denied({"model_id": "b14-same-name", "model_record_id": str(models[2])}, "model_unavailable")
        await denied({"model_id": "b14-wrong-provider-owner", "model_record_id": str(models[3])}, "model_unavailable")
        await denied({"model_id": "different-name", "model_record_id": str(models[0])}, "model_unavailable")
        await denied({"model_id": "b14-same-name", "model_record_id": str(uuid4())}, "model_unavailable")
        before = len(captured)
        invalid = await client.post(endpoint, headers=headers(owner_id), json={"model_id": "b14-same-name", "model_record_id": "not-a-uuid"})
        check("b14_invalid_UUID_422_no_model", invalid.status_code == 422 and len(captured) == before and bool(invalid.headers.get("X-Request-ID")))
        async with scoped() as session:
            await session.execute(update(AIProvider).where(AIProvider.id == providers[1]).values(is_active=False))
            await session.commit()
        await denied({"model_id": "b14-same-name", "model_record_id": str(models[1])}, "model_unavailable")
        unique = await client.post(endpoint, headers=headers(owner_id), json={"model_id": "b14-same-name"})
        check("b14_unique_active_legacy_route_compatible", unique.status_code == 200 and "Synthetic selected provider response" in unique.text and captured[-1] == ("https://unused-0.invalid", "b14-same-name"))
        async with scoped() as session:
            await session.execute(update(AIProvider).where(AIProvider.id == providers[1]).values(is_active=True))
            await session.commit()
            before = len(captured)
            events = [event async for event in gateway.chat_stream(session, str(owner_id), "b14-same-name", [], tools_enabled=False)]
            check("b14_text_only_summary_cannot_guess_ambiguous_provider", events[0].extra.get("error_code") == "model_ambiguous" and len(captured) == before)
    async with scoped() as session:
        check("b14_public_provider_model_tables_unchanged", [await session.scalar(text(f"SELECT count(*) FROM public.{table}")) for table in ("ai_models", "ai_providers")] == public_counts)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b14"))
