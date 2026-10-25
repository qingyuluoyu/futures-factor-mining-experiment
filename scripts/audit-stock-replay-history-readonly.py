import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Dedicated local PG5433 only")
    engine = create_replay_engine(settings)
    report = {"status": "running", "source_calls": 0, "model_calls": 0, "public_business_writes": 0,
        "scope": "public_aggregate_integrity_not_private_user_authorization_or_endpoint_acceptance", "tables": {},
        "real_user_endpoint_validation": "not_executed_no_user_session_supplied_do_not_impersonate_or_create_public_test_account"}
    output = ROOT / "logs" / f"stock-replay-history-readonly-{uuid4().hex}.json"
    try:
        async with asyncio.timeout(35), engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout='5s'"))
            readonly = await connection.scalar(text("SHOW transaction_read_only"))
            isolation = await connection.scalar(text("SHOW transaction_isolation"))
            if readonly != "on" or isolation != "repeatable read":
                raise RuntimeError("Readonly audit guard failed")
            for table in ("stock_replay_datasets", "stock_replay_accounts", "stock_replay_orders", "stock_replay_steps"):
                count = await connection.scalar(text(f"SELECT count(*) FROM public.{table}"))
                if count > 100000:
                    raise RuntimeError("Audit exceeds bounded hash capacity")
                digest = await connection.scalar(text(f"SELECT md5(string_agg(row_to_json(item)::text, '' ORDER BY row_to_json(item)::text)) FROM public.{table} AS item"))
                report["tables"][table] = {"rows": count, "checksum": digest}
            report.update(status="passed", read_only=readonly, isolation=isolation, checked_at=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=True, allow_nan=False, indent=2)
    print(json.dumps({"status": report["status"], "output": output.name, "source_calls": 0, "model_calls": 0, "public_business_writes": 0}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
