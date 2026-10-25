"""原子迁移独立回放表；--check 仅校验版本及结构，不修改数据库。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
MIGRATION_PATH = BACKEND_ROOT / "migrations" / "20261017_001_stock_replay.sql"
MIGRATION_VERSION = "20261017_001_stock_replay"
ADVISORY_LOCK_ID = 20261017001

sys.path.insert(0, str(BACKEND_ROOT))

from app.models.stock_replay import ReplayBase


def load_runtime_settings():
    from app.core.config import load_settings

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(PROJECT_ROOT / ".env.runtime", override=True)
    return load_settings()


def create_replay_engine(settings) -> AsyncEngine:
    database_url = URL.create(
        "postgresql+asyncpg", username=settings.postgres_user, password=settings.postgres_password,
        host=settings.postgres_host, port=settings.postgres_port, database=settings.postgres_db,
    )
    return create_async_engine(
        database_url, echo=False, pool_pre_ping=True,
        connect_args={"timeout": 10, "command_timeout": 30},
    )


async def validate_schema(connection: AsyncConnection) -> None:
    def validate(sync_connection):
        inspector = inspect(sync_connection)
        dialect = postgresql.dialect()
        for table in ReplayBase.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"missing replay table: {table.name}")
            columns = {column["name"]: column for column in inspector.get_columns(table.name)}
            if set(columns) != set(table.columns.keys()):
                raise RuntimeError(f"schema column mismatch: {table.name}")
            for expected in table.columns:
                actual = columns[expected.name]
                if (actual["nullable"] != expected.nullable or
                    actual["type"].compile(dialect=dialect) != expected.type.compile(dialect=dialect)):
                    raise RuntimeError(f"schema type/nullability mismatch: {table.name}.{expected.name}")
            primary = inspector.get_pk_constraint(table.name)["constrained_columns"]
            if primary != [column.name for column in table.primary_key.columns]:
                raise RuntimeError(f"schema primary key mismatch: {table.name}")
            actual_unique = {tuple(value["column_names"]) for value in inspector.get_unique_constraints(table.name)}
            expected_unique = {tuple(column.name for column in value.columns) for value in table.constraints if isinstance(value, UniqueConstraint)}
            if not expected_unique.issubset(actual_unique):
                raise RuntimeError(f"schema unique constraint mismatch: {table.name}")
            actual_checks = {value["name"] for value in inspector.get_check_constraints(table.name)}
            expected_checks = {value.name for value in table.constraints if isinstance(value, CheckConstraint)}
            if not expected_checks.issubset(actual_checks):
                raise RuntimeError(f"schema check constraint mismatch: {table.name}")
            actual_indexes = {value["name"]: tuple(value["column_names"]) for value in inspector.get_indexes(table.name)}
            for expected in table.indexes:
                if actual_indexes.get(expected.name) != tuple(column.name for column in expected.columns):
                    raise RuntimeError(f"schema index mismatch: {table.name}.{expected.name}")
            actual_foreign = {
                (tuple(value["constrained_columns"]), value["referred_table"], tuple(value["referred_columns"]), value.get("options", {}).get("ondelete"))
                for value in inspector.get_foreign_keys(table.name)
            }
            for expected in table.foreign_key_constraints:
                target = (
                    tuple(value.parent.name for value in expected.elements), expected.referred_table.name,
                    tuple(value.column.name for value in expected.elements), expected.ondelete,
                )
                if target not in actual_foreign:
                    raise RuntimeError(f"schema foreign key mismatch: {table.name}")

    await connection.run_sync(validate)


async def migrate_stock_replay(engine: AsyncEngine, *, check: bool = False) -> dict:
    migration_bytes = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(migration_bytes).hexdigest()
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        await connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID})
        if check:
            exists = (await connection.execute(text("SELECT to_regclass('stock_replay_schema_versions')"))).scalar_one_or_none()
            if exists is None:
                raise RuntimeError("stock replay migration not applied: missing schema version table")
        else:
            await connection.execute(text(
                "CREATE TABLE IF NOT EXISTS stock_replay_schema_versions ("
                "version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            ))
        existing = (await connection.execute(
            text("SELECT checksum FROM stock_replay_schema_versions WHERE version = :version"),
            {"version": MIGRATION_VERSION},
        )).scalar_one_or_none()
        if existing is not None and existing != checksum:
            raise RuntimeError("stock replay migration checksum mismatch; create a new migration instead of modifying applied SQL")
        if existing is None:
            if check:
                raise RuntimeError("stock replay migration not applied")
            for statement in migration_bytes.decode("utf-8").split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await validate_schema(connection)
            await connection.execute(
                text("INSERT INTO stock_replay_schema_versions (version, checksum) VALUES (:version, :checksum)"),
                {"version": MIGRATION_VERSION, "checksum": checksum},
            )
        else:
            await validate_schema(connection)
    return {"version": MIGRATION_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


async def _main(check: bool) -> None:
    engine = create_replay_engine(load_runtime_settings())
    try:
        print(json.dumps(await migrate_stock_replay(engine, check=check)))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验结构和版本，不创建表")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args.check))
    except Exception as exc:
        print(f"stock replay migration failed ({type(exc).__name__}); no partial migration committed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
