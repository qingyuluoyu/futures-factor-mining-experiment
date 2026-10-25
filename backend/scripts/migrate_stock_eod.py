"""原子迁移独立股票盘后快照表；--check 仅校验版本及结构，不修改数据库。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "migrations" / "20261018_003_stock_eod.sql"
MIGRATION_VERSION = "20261018_003_stock_eod"
ADVISORY_LOCK_ID = 20261018004

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.stock_eod import EodBase
from migrate_stock_replay import create_replay_engine, load_runtime_settings
from migrate_stock_research_jobs import _check_signature


async def validate_schema(connection: AsyncConnection) -> None:
    def validate(sync_connection):
        inspector = inspect(sync_connection)
        dialect = postgresql.dialect()
        for table in EodBase.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"missing EOD table: {table.name}")
            columns = {column["name"]: column for column in inspector.get_columns(table.name)}
            if set(columns) != set(table.columns.keys()):
                raise RuntimeError(f"schema column mismatch: {table.name}")
            for expected in table.columns:
                actual = columns[expected.name]
                if (actual["nullable"] != expected.nullable or
                    actual["type"].compile(dialect=dialect) != expected.type.compile(dialect=dialect)):
                    raise RuntimeError(f"schema type/nullability mismatch: {table.name}.{expected.name}")
            if inspector.get_pk_constraint(table.name)["constrained_columns"] != [column.name for column in table.primary_key.columns]:
                raise RuntimeError(f"schema primary key mismatch: {table.name}")
            actual_unique = {tuple(value["column_names"]) for value in inspector.get_unique_constraints(table.name)}
            expected_unique = {tuple(column.name for column in value.columns) for value in table.constraints if isinstance(value, UniqueConstraint)}
            if actual_unique != expected_unique:
                raise RuntimeError(f"schema unique constraint mismatch: {table.name}")
            actual_checks = {value["name"]: value["sqltext"] for value in inspector.get_check_constraints(table.name)}
            expected_checks = {value.name: str(value.sqltext) for value in table.constraints if isinstance(value, CheckConstraint)}
            if set(actual_checks) != set(expected_checks):
                raise RuntimeError(f"schema check constraint mismatch: {table.name}")
            for name, expression in expected_checks.items():
                if _check_signature(actual_checks[name]) != _check_signature(expression):
                    raise RuntimeError(f"schema check expression mismatch: {table.name}.{name}")
            actual_indexes = {value["name"]: value for value in inspector.get_indexes(table.name)}
            for expected in table.indexes:
                actual = actual_indexes.get(expected.name)
                if (actual is None or tuple(actual["column_names"]) != tuple(column.name for column in expected.columns)
                    or bool(actual.get("unique")) != bool(expected.unique)
                    or actual.get("dialect_options", {}).get("postgresql_where") is not None):
                    raise RuntimeError(f"schema index mismatch: {table.name}.{expected.name}")
            if inspector.get_foreign_keys(table.name):
                raise RuntimeError(f"schema foreign key mismatch: {table.name}")

    await connection.run_sync(validate)


async def migrate_stock_eod(engine: AsyncEngine, *, check: bool = False) -> dict:
    migration_bytes = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(migration_bytes).hexdigest()
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        await connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID})
        if check:
            exists = (await connection.execute(text("SELECT to_regclass('stock_eod_schema_versions')"))).scalar_one_or_none()
            if exists is None:
                raise RuntimeError("stock EOD migration not applied: missing schema version table")
        else:
            await connection.execute(text(
                "CREATE TABLE IF NOT EXISTS stock_eod_schema_versions ("
                "version VARCHAR(64) PRIMARY KEY, checksum CHAR(64) NOT NULL)"
            ))
        existing = (await connection.execute(
            text("SELECT checksum FROM stock_eod_schema_versions WHERE version = :version"),
            {"version": MIGRATION_VERSION},
        )).scalar_one_or_none()
        if existing is not None and existing != checksum:
            raise RuntimeError("stock EOD migration checksum mismatch; create a new migration instead of modifying applied SQL")
        if existing is None:
            if check:
                raise RuntimeError("stock EOD migration not applied")
            for statement in migration_bytes.decode("utf-8").split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await validate_schema(connection)
            await connection.execute(
                text("INSERT INTO stock_eod_schema_versions (version, checksum) VALUES (:version, :checksum)"),
                {"version": MIGRATION_VERSION, "checksum": checksum},
            )
        else:
            await validate_schema(connection)
    return {"version": MIGRATION_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


async def _main(check: bool) -> None:
    engine = create_replay_engine(load_runtime_settings())
    try:
        print(json.dumps(await migrate_stock_eod(engine, check=check)))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验结构和版本，不创建表")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args.check))
    except Exception as error:
        print(f"stock EOD migration failed ({type(error).__name__}); no partial migration committed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
