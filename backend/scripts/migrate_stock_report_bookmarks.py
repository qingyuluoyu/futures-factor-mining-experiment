import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.models.stock_report_bookmark import BookmarkBase
from app.services.stock_report_bookmarks import SCHEMA_PATH, SCHEMA_VERSION
from migrate_stock_replay import create_replay_engine, load_runtime_settings


async def validate_schema(connection):
    schema = await connection.scalar(text("SELECT current_schema()"))

    def validate(sync_connection):
        inspector = inspect(sync_connection)
        for table in BookmarkBase.metadata.sorted_tables:
            if not inspector.has_table(table.name, schema=schema):
                raise RuntimeError("bookmark table missing")
            columns = {column["name"]: column for column in inspector.get_columns(table.name, schema=schema)}
            if set(columns) != set(table.columns.keys()):
                raise RuntimeError("bookmark columns mismatch")
            for expected in table.columns:
                actual = columns[expected.name]
                if actual["nullable"] != expected.nullable or actual["type"].compile(dialect=postgresql.dialect()) != expected.type.compile(dialect=postgresql.dialect()):
                    raise RuntimeError("bookmark column type mismatch")
                if expected.server_default is not None and str(actual.get("default", "")).lower().replace(" ", "") not in {"current_timestamp", "now()", "transaction_timestamp()"}:
                    raise RuntimeError("bookmark timestamp default mismatch")
            if inspector.get_pk_constraint(table.name, schema=schema)["constrained_columns"] != [column.name for column in table.primary_key.columns]:
                raise RuntimeError("bookmark primary key mismatch")
        indexes = {item["name"]: item for item in inspector.get_indexes("stock_report_bookmarks", schema=schema)}
        index = indexes.get("ix_stock_report_bookmark_created")
        if index is None or index["column_names"] != ["created_at", "job_id"] or index.get("unique") or index.get("dialect_options", {}).get("postgresql_where"):
            raise RuntimeError("bookmark index mismatch")
        foreign = inspector.get_foreign_keys("stock_report_bookmarks", schema=schema)
        if len(foreign) != 1 or foreign[0]["constrained_columns"] != ["job_id"] or foreign[0]["referred_table"] != "stock_research_jobs" or foreign[0]["referred_columns"] != ["id"] or foreign[0].get("referred_schema") not in (None, schema) or foreign[0].get("options", {}).get("ondelete") != "RESTRICT":
            raise RuntimeError("bookmark ownership reference mismatch")

    await connection.run_sync(validate)


async def migrate_stock_report_bookmarks(engine, *, check=True):
    checksum = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    async with engine.begin() as connection:
        if check:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(text("SET LOCAL lock_timeout='5s'"))
        await connection.execute(text("SET LOCAL statement_timeout='30s'"))
        parent = await connection.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.stock_research_jobs')"))
        if parent is None:
            raise RuntimeError("stock research migration required first")
        if not check:
            await connection.execute(text("SELECT pg_advisory_xact_lock(20261022014)"))
            await connection.execute(text("CREATE TABLE IF NOT EXISTS stock_report_bookmark_schema_versions (version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
        exists = await connection.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.stock_report_bookmark_schema_versions')"))
        if exists is None:
            raise RuntimeError("bookmark migration not applied")
        existing = await connection.scalar(text("SELECT checksum FROM stock_report_bookmark_schema_versions WHERE version=:version"), {"version": SCHEMA_VERSION})
        if existing is not None and existing != checksum:
            raise RuntimeError("bookmark migration checksum mismatch")
        if existing is None:
            if check:
                raise RuntimeError("bookmark migration not applied")
            for statement in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await validate_schema(connection)
            await connection.execute(text("INSERT INTO stock_report_bookmark_schema_versions(version,checksum) VALUES (:version,:checksum)"), {"version": SCHEMA_VERSION, "checksum": checksum})
        else:
            await validate_schema(connection)
    return {"version": SCHEMA_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


async def run(check):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost", "::1") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    engine = create_replay_engine(settings)
    try:
        print(json.dumps(await migrate_stock_report_bookmarks(engine, check=check)))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Versioned additive stock report bookmarks; defaults to read-only check")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(not arguments.apply))
    except Exception as error:
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"bookmark migration failed: {reason}; transaction rolled back", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
