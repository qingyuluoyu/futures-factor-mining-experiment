import asyncio
import json
from pathlib import Path
import re
import sys
import time

import httpx
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    checks = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for path, expected in (("/login", 200), ("/dashboard", 200), ("/api/ai/chat/capabilities", 401),
                               ("/api/users/me/notify-settings", 401), ("/api/ai/chat/requests/11faa4f4-706c-4867-9ede-c56a30642fc5", 401)):
            started = time.perf_counter()
            response = await client.get("http://127.0.0.1:3001" + path)
            checks.append({"path": path, "status": response.status_code, "passed": response.status_code == expected,
                           "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
            if path == "/login":
                assets = list(dict.fromkeys(re.findall(r'src="(/_next/[^"?]+\.js)', response.text)))[:4]
                for asset in assets:
                    resource = await client.get("http://127.0.0.1:3001" + asset)
                    checks.append({"asset": asset, "status": resource.status_code, "passed": resource.status_code == 200})
        runtime = (await client.get("http://127.0.0.1:8002/local-runtime/status")).json()
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated PG required")
    engine = create_replay_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            tasks = [dict(row) for row in (await connection.execute(text("SELECT status,pause_reason,count(*) AS count FROM ai_trading_tasks GROUP BY status,pause_reason"))).mappings()]
            request = (await connection.execute(text("SELECT status,error_code,cost_state,usage_json FROM plain_chat_requests WHERE id='11faa4f4-706c-4867-9ede-c56a30642fc5'"))).mappings().one_or_none()
            paid = dict(request) if request else None
    finally:
        await engine.dispose()
    report = {"http": checks, "runtime": runtime, "ai_task_states": tasks, "single_browser_request": paid,
              "new_paid_calls_by_this_verifier": 0, "public_business_writes": 0,
              "scope": "HTTP_delivery_not_real_browser_performance_or_full_product_acceptance"}
    (ROOT / "logs/ux-runtime-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(report, ensure_ascii=True))
    if not all(item["passed"] for item in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
