import argparse
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings
from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.services.crypto import decrypt
from app.services.ai.providers.openai_provider import provider_error
from app.services.ai.providers.urls import resolve_endpoint


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Dedicated local PG5433 required")
    database = create_replay_engine(settings)
    factory = async_sessionmaker(database)
    try:
        async with factory() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = (await session.execute(select(AIModel.model_id, AIProvider.base_url, AIProvider.api_key).join(AIProvider, AIModel.provider_id == AIProvider.id).where(AIModel.model_id == "deepseek-flash", AIModel.user_id == AIProvider.user_id))).all()
            if len(rows) != 1:
                print(json.dumps({"status": "not_called", "reason": "ambiguous_or_missing_model", "matches": len(rows)}))
                return
            model, base_url, encrypted = rows[0]
            url = resolve_endpoint(base_url, "/chat/completions")
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname != "token.sensenova.cn" or parsed.port not in (443, None) or parsed.username or parsed.query:
                print(json.dumps({"status": "not_called", "reason": "configured_destination_not_previous_user_authorized_endpoint"}))
                return
            key = decrypt(encrypted)
        report = {"calls": 1, "retry": 0, "output_token_limit": 32, "timeout_seconds": 20, "tools": False, "private_context_sent": False, "public_writes": 0}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.post(url, headers={"Authorization": "Bearer " + key}, json={"model": model, "messages": [{"role": "user", "content": "仅回复：连接成功"}], "max_tokens": 32, "stream": False})
                report["http_status"] = response.status_code
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                if response.status_code == 200 and isinstance(payload, dict) and not payload.get("error"):
                    choices = payload.get("choices") or []
                    content = choices[0].get("message", {}).get("content", "") if choices else ""
                    report["status"] = "text_received" if isinstance(content, str) and content.strip() else "empty_response"
                    report["reply_characters"] = len(content) if isinstance(content, str) else 0
                    usage = payload.get("usage") or {}
                    report["usage"] = {field: usage[field] for field in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage.get(field), int)}
                else:
                    report["status"] = provider_error(payload, response.status_code).extra["error_code"]
        except httpx.TimeoutException:
            report["status"] = "provider_timeout"
        except httpx.RequestError:
            report["status"] = "provider_connection_failed"
        print(json.dumps(report, ensure_ascii=False))
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One explicitly budgeted minimal configured SenseNova request; no automatic retries")
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(main())
