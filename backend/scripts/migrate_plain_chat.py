import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import inspect, text


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings

VERSION = "20261021_014_plain_chat"
SQL = ROOT / "backend/migrations" / f"{VERSION}.sql"
EXPECTED = {"id", "user_id", "trading_mode", "market_type", "model_record_id", "params_hash", "prompt_version", "status", "error_code", "response_text", "usage_json", "cost_state", "started_at", "deadline_at", "finished_at"}


async def validate(connection):
    def inspect_schema(sync):
        inspector = inspect(sync)
        columns = {item["name"]: item for item in inspector.get_columns("plain_chat_requests")}
        if set(columns) != EXPECTED:
            raise RuntimeError("plain chat columns mismatch")
        nullable = {"error_code", "usage_json", "finished_at"}
        if any(item["nullable"] != (name in nullable) for name, item in columns.items()):
            raise RuntimeError("plain chat nullability mismatch")
        if inspector.get_pk_constraint("plain_chat_requests")["constrained_columns"] != ["id"]:
            raise RuntimeError("plain chat primary key mismatch")
        indexes = {item["name"]: item for item in inspector.get_indexes("plain_chat_requests")}
        running = indexes.get("uq_plain_chat_user_running", {})
        if not running.get("unique") or running.get("column_names") != ["user_id"] or "running" not in str(running.get("dialect_options", {}).get("postgresql_where", "")):
            raise RuntimeError("plain chat concurrency index mismatch")
        if len(inspector.get_check_constraints("plain_chat_requests")) != 5:
            raise RuntimeError("plain chat state constraints mismatch")
    await connection.run_sync(inspect_schema)


async def migrate(connection, *, apply=False):
    await connection.execute(text("SET LOCAL lock_timeout='2s'"))
    await connection.execute(text("SET LOCAL statement_timeout='30s'"))
    await connection.execute(text("SELECT pg_advisory_xact_lock(20261021014)"))
    payload = SQL.read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    exists = await connection.scalar(text("SELECT to_regclass('plain_chat_schema_versions') IS NOT NULL"))
    if not exists:
        if not apply:
            raise RuntimeError("plain chat migration not applied")
        if await connection.scalar(text("SELECT to_regclass('plain_chat_requests') IS NOT NULL")):
            raise RuntimeError("unversioned plain chat table exists")
        for statement in payload.decode("utf8").split(";"):
            if statement.strip():
                await connection.execute(text(statement))
        await connection.execute(text("INSERT INTO plain_chat_schema_versions(version,checksum) VALUES (:version,:checksum)"), {"version": VERSION, "checksum": checksum})
    elif await connection.scalar(text("SELECT checksum FROM plain_chat_schema_versions WHERE version=:version"), {"version": VERSION}) != checksum:
        raise RuntimeError("plain chat migration version/checksum mismatch")
    await validate(connection)
    return {"version": VERSION, "checked": True, "applied": not exists, "checksum": checksum}


async def run(apply):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated local PG5433 required")
    engine = create_replay_engine(settings)
    try:
        async with engine.begin() as connection:
            return await migrate(connection, apply=apply)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Check plain-chat schema; --apply performs atomic compatible expansion")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.apply and arguments.check:
        parser.error("choose check or apply")
    try:
        print(json.dumps(asyncio.run(run(arguments.apply))))
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__, "transaction_rolled_back": True}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
