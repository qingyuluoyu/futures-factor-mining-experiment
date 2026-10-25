"""原子迁移研究任务表；--check 仅校验版本及结构，不修改数据库。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "migrations" / "20261018_002_stock_research_jobs.sql"
MIGRATION_VERSION = "20261018_002_stock_research_jobs"
ADVISORY_LOCK_ID = 20261018002

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.stock_research_job import ResearchJobBase
from migrate_stock_replay import create_replay_engine, load_runtime_settings


def _check_signature(expression: str):
    expression = re.sub(r"::(?:character varying|text|bpchar)\b", "", expression, flags=re.IGNORECASE)
    expression = re.sub(r"\(([A-Za-z_][A-Za-z_0-9]*|'(?:[^']|'')*'|-?\d+)\)", r"\1", expression)
    pattern = re.compile(r"'(?:[^']|'')*'|[A-Za-z_][A-Za-z_0-9]*|<>|!=|<=|>=|[()=<>]|-?\d+")
    tokens = []
    position = 0
    for match in pattern.finditer(expression):
        if expression[position:match.start()].strip():
            raise RuntimeError("unsupported schema check expression")
        token = match.group()
        tokens.append(token if token.startswith("'") else token.lower().replace("!=", "<>"))
        position = match.end()
    if expression[position:].strip() or not tokens:
        raise RuntimeError("unsupported schema check expression")
    cursor = 0

    def combine(operator, nodes):
        flattened = []
        for node in nodes:
            if node[0] == operator:
                flattened.extend(node[1:])
            else:
                flattened.append(node)
        return flattened[0] if len(flattened) == 1 else (operator, *flattened)

    def parse_or():
        nonlocal cursor
        nodes = [parse_and()]
        while cursor < len(tokens) and tokens[cursor] == "or":
            cursor += 1
            nodes.append(parse_and())
        return combine("or", nodes)

    def parse_and():
        nonlocal cursor
        nodes = [parse_term()]
        while cursor < len(tokens) and tokens[cursor] == "and":
            cursor += 1
            nodes.append(parse_term())
        return combine("and", nodes)

    def parse_term():
        nonlocal cursor
        if cursor < len(tokens) and tokens[cursor] == "(":
            cursor += 1
            node = parse_or()
            if cursor >= len(tokens) or tokens[cursor] != ")":
                raise RuntimeError("unsupported schema check grouping")
            cursor += 1
            return node
        start = cursor
        while cursor < len(tokens) and tokens[cursor] not in ("and", "or", ")"):
            cursor += 1
        if start == cursor:
            raise RuntimeError("unsupported schema check predicate")
        return ("predicate", *tokens[start:cursor])

    signature = parse_or()
    if cursor != len(tokens):
        raise RuntimeError("unsupported schema check expression")
    return signature


async def validate_schema(connection: AsyncConnection) -> None:
    def validate(sync_connection):
        inspector = inspect(sync_connection)
        dialect = postgresql.dialect()
        for table in ResearchJobBase.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"missing research table: {table.name}")
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
            actual_foreign = {
                (tuple(value["constrained_columns"]), value["referred_table"], tuple(value["referred_columns"]), value.get("options", {}).get("ondelete"))
                for value in inspector.get_foreign_keys(table.name)
            }
            expected_foreign = {
                (tuple(value.parent.name for value in expected.elements), expected.referred_table.name,
                 tuple(value.column.name for value in expected.elements), expected.ondelete)
                for expected in table.foreign_key_constraints
            }
            if actual_foreign != expected_foreign:
                raise RuntimeError(f"schema foreign key mismatch: {table.name}")

    await connection.run_sync(validate)


async def migrate_stock_research_jobs(engine: AsyncEngine, *, check: bool = False) -> dict:
    migration_bytes = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(migration_bytes).hexdigest()
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        await connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID})
        if check:
            exists = (await connection.execute(text("SELECT to_regclass('stock_research_schema_versions')"))).scalar_one_or_none()
            if exists is None:
                raise RuntimeError("stock research migration not applied: missing schema version table")
        else:
            await connection.execute(text(
                "CREATE TABLE IF NOT EXISTS stock_research_schema_versions ("
                "version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            ))
        existing = (await connection.execute(
            text("SELECT checksum FROM stock_research_schema_versions WHERE version = :version"),
            {"version": MIGRATION_VERSION},
        )).scalar_one_or_none()
        if existing is not None and existing != checksum:
            raise RuntimeError("stock research migration checksum mismatch; create a new migration instead of modifying applied SQL")
        if existing is None:
            if check:
                raise RuntimeError("stock research migration not applied")
            for statement in migration_bytes.decode("utf-8").split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await validate_schema(connection)
            await connection.execute(
                text("INSERT INTO stock_research_schema_versions (version, checksum) VALUES (:version, :checksum)"),
                {"version": MIGRATION_VERSION, "checksum": checksum},
            )
        else:
            await validate_schema(connection)
    return {"version": MIGRATION_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


async def _main(check: bool) -> None:
    engine = create_replay_engine(load_runtime_settings())
    try:
        print(json.dumps(await migrate_stock_research_jobs(engine, check=check)))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验结构和版本，不创建表")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args.check))
    except Exception as exc:
        print(f"stock research migration failed ({type(exc).__name__}); no partial migration committed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
