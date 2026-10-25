import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_eod_reconciliation import read_reconciliation
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


def parse_arguments(arguments=None):
    def version(value):
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise argparse.ArgumentTypeError("必须使用明确的SHA256版本，不接受latest")
        return value

    parser = argparse.ArgumentParser(description="仅用本地已存数据对账两个固定盘后版本，不采集或写业务库")
    parser.add_argument("--previous", required=True, type=version)
    parser.add_argument("--current", required=True, type=version)
    parsed = parser.parse_args(arguments)
    if parsed.previous == parsed.current:
        parser.error("previous和current必须是不同版本")
    return parsed


def validate_local_settings(settings):
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise ValueError("仅允许专用本地PG5433")


async def collect_report(factory, previous: str, current: str, *, now: datetime) -> dict:
    async with factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout='8s'"))
        await session.execute(text("SET LOCAL lock_timeout='2s'"))
        readonly = await session.scalar(text("SHOW transaction_read_only"))
        isolation = await session.scalar(text("SHOW transaction_isolation"))
        if readonly != "on" or isolation != "repeatable read":
            raise RuntimeError("只读可重复读事务未生效")
        result = await read_reconciliation(session, previous, current, now=now)
        return {"status": "observed", "request_id": uuid4().hex,
                "transaction_read_only": readonly, "transaction_isolation": isolation,
                "operator_scope": "trusted_local_database_operator_not_user_HTTP_authentication",
                "public_business_writes": 0, "result": result}


async def run(arguments) -> dict:
    settings = load_runtime_settings()
    validate_local_settings(settings)
    engine = create_replay_engine(settings)
    try:
        async with asyncio.timeout(30):
            return await collect_report(async_sessionmaker(engine, expire_on_commit=False), arguments.previous, arguments.current,
                                        now=datetime.now(timezone.utc))
    finally:
        await engine.dispose()


def failure_report(error: Exception) -> dict:
    return {"status": "failed", "error_type": type(error).__name__, "request_id": uuid4().hex,
            "scope": "local_saved_data_audit_failed_not_zero_gap_certification",
            "source_calls": 0, "model_calls": 0, "public_business_writes": 0}


def write_report(report: dict, root: Path = ROOT) -> Path:
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)
    output = root / "logs" / f"stock-eod-reconciliation-{uuid4().hex}.json"
    with output.open("x", encoding="utf-8") as destination:
        destination.write(payload)
    return output


def main(arguments=None) -> int:
    parsed = parse_arguments(arguments)
    try:
        report = asyncio.run(run(parsed))
    except Exception as error:
        report = failure_report(error)
    output = write_report(report)
    print(json.dumps({"status": report["status"], "request_id": report["request_id"], "report": str(output)}, ensure_ascii=True))
    return 0 if report["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
