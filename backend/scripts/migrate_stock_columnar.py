import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.models.stock_columnar import ColumnarBase
from app.services.stock_research_v2.columnar import SCHEMA_PATH, SCHEMA_VERSION
from migrate_stock_research_datasets import (validate_schema as validate_parent_schema, SCHEMA_PATH as PARENT_SCHEMA_PATH, SCHEMA_VERSION as PARENT_SCHEMA_VERSION)
from migrate_stock_replay import create_replay_engine, load_runtime_settings


def normalize_check(expression):
    value = re.sub(r"::(?:character varying|text|integer)(?:\[\])?", "", expression.lower())
    value = re.sub(r'[\s()"]', "", value)
    value = re.sub(r"=anyarray\[([^\]]+)\]", r"in\1", value)
    return re.sub(r"([a-z_]+)between([0-9]+)and([0-9]+)", r"\1>=\2and\1<=\3", value)


async def validate_schema(connection):
    schema = await connection.scalar(text("SELECT current_schema()"))

    def validate(sync_connection):
        inspector = inspect(sync_connection)
        for table in ColumnarBase.metadata.sorted_tables:
            if not inspector.has_table(table.name, schema=schema):
                raise RuntimeError("stock columnar table missing")
            actual = {item["name"]: item for item in inspector.get_columns(table.name, schema=schema)}
            if set(actual) != set(table.columns.keys()):
                raise RuntimeError("stock columnar columns mismatch")
            for column in table.columns:
                found = actual[column.name]
                if found["nullable"] != column.nullable or found["type"].compile(dialect=postgresql.dialect()) != column.type.compile(dialect=postgresql.dialect()):
                    raise RuntimeError("stock columnar column type mismatch")
                default = str(found.get("default") or "").lower().replace(" ", "")
                if (column.server_default is not None and default not in {"current_timestamp", "now()", "transaction_timestamp()"}) or (column.server_default is None and default):
                    raise RuntimeError("stock columnar default mismatch")
            if inspector.get_pk_constraint(table.name, schema=schema)["constrained_columns"] != [item.name for item in table.primary_key.columns]:
                raise RuntimeError("stock columnar primary key mismatch")
            expected_checks = {item.name: normalize_check(str(item.sqltext)) for item in table.constraints if hasattr(item, "sqltext")}
            actual_checks = {item["name"]: normalize_check(item["sqltext"]) for item in inspector.get_check_constraints(table.name, schema=schema)}
            if actual_checks != expected_checks:
                raise RuntimeError("stock columnar check mismatch")
            expected_unique = {item.name: [column.name for column in item.columns] for item in table.constraints if item.__class__.__name__ == "UniqueConstraint"}
            actual_unique = {item["name"]: item["column_names"] for item in inspector.get_unique_constraints(table.name, schema=schema)}
            if actual_unique != expected_unique:
                raise RuntimeError("stock columnar uniqueness mismatch")
            expected_foreign = {(tuple(item.column_keys), next(iter(item.elements)).column.table.name, tuple(part.column.name for part in item.elements), item.ondelete) for item in table.foreign_key_constraints}
            actual_foreign = set()
            for item in inspector.get_foreign_keys(table.name, schema=schema):
                if item.get("referred_schema") not in (None, schema):
                    raise RuntimeError("stock columnar foreign schema mismatch")
                actual_foreign.add((tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]), item.get("options", {}).get("ondelete")))
            if actual_foreign != expected_foreign:
                raise RuntimeError("stock columnar reference mismatch")
            expected_indexes = {item.name: ([part.name for part in item.columns], item.unique) for item in table.indexes}
            actual_indexes = {}
            for item in inspector.get_indexes(table.name, schema=schema):
                if item.get("duplicates_constraint"):
                    continue
                if item.get("dialect_options", {}).get("postgresql_where"):
                    raise RuntimeError("stock columnar partial index mismatch")
                actual_indexes[item["name"]] = (item["column_names"], bool(item["unique"]))
            if actual_indexes != expected_indexes:
                raise RuntimeError("stock columnar index mismatch")

    await connection.run_sync(validate)
    unvalidated = await connection.scalar(text("SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema() AND t.relname IN ('stock_research_columnar_datasets','stock_research_columnar_schema_versions') AND NOT c.convalidated"))
    if unvalidated:
        raise RuntimeError("stock columnar constraints not validated")


def migration_statements(sql):
    # This allowlist is for the fixed 018 DDL, not a general-purpose SQL parser.
    statements = [part.strip() for part in re.sub(r"--[^\n]*", "", sql).split(";") if part.strip()]
    allowed = r"CREATE\s+(?:TABLE\s+IF\s+NOT\s+EXISTS\s+stock_research_columnar_(?:datasets|schema_versions)|INDEX\s+IF\s+NOT\s+EXISTS\s+ix_stock_columnar_dataset_scope\s+ON\s+stock_research_columnar_datasets)\s*\("
    if not statements or any(not re.match(allowed, statement, re.IGNORECASE) for statement in statements):
        raise RuntimeError("columnar migration contains unsupported DDL")
    return statements


async def migrate_stock_columnar(engine, *, check=True):
    checksum = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    async with engine.begin() as connection:
        if check:
            await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(text("SET LOCAL lock_timeout='5s'"))
        await connection.execute(text("SET LOCAL statement_timeout='30s'"))
        # 018 never applies or silently repairs its 015 prerequisite.
        await validate_parent_schema(connection)
        parent_checksum = await connection.scalar(text("SELECT checksum FROM stock_research_dataset_schema_versions WHERE version=:version"), {"version": PARENT_SCHEMA_VERSION})
        if parent_checksum != hashlib.sha256(PARENT_SCHEMA_PATH.read_bytes()).hexdigest():
            raise RuntimeError("verified stock dataset migration 015 required first")
        if not check:
            await connection.execute(text("SELECT pg_advisory_xact_lock(20261024018)"))
            await connection.execute(text("CREATE TABLE IF NOT EXISTS stock_research_columnar_schema_versions (version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
        if await connection.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.stock_research_columnar_schema_versions')")) is None:
            raise RuntimeError("stock columnar migration not applied")
        existing = await connection.scalar(text("SELECT checksum FROM stock_research_columnar_schema_versions WHERE version=:version"), {"version": SCHEMA_VERSION})
        if existing is not None and existing != checksum:
            raise RuntimeError("stock columnar migration checksum mismatch")
        if existing is None:
            if check:
                raise RuntimeError("stock columnar migration not applied")
            for statement in migration_statements(SCHEMA_PATH.read_text(encoding="utf8")):
                await connection.execute(text(statement))
            await validate_schema(connection)
            await connection.execute(text("INSERT INTO stock_research_columnar_schema_versions(version,checksum) VALUES (:version,:checksum)"), {"version": SCHEMA_VERSION, "checksum": checksum})
        else:
            await validate_schema(connection)
    return {"version": SCHEMA_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


async def run(check):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost", "::1") or settings.postgres_port != 5433:
        raise RuntimeError("dedicated loopback PG5433 required")
    engine = create_replay_engine(settings)
    try:
        print(json.dumps(await migrate_stock_columnar(engine, check=check)))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Additive stock columnar registry; defaults to read-only check")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(not arguments.apply))
    except Exception as error:
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"stock columnar migration failed: {reason}; transaction rolled back", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
