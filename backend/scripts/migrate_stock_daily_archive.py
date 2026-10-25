"""原子迁移独立股票原价日线归档；--check 仅校验，不创建或补建表。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "migrations" / "20261019_004_stock_daily_archive.sql"
MIGRATION_VERSION = "20261019_004_stock_daily_archive"
ADVISORY_LOCK_ID = 20261019004

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.stock_daily_archive import ArchiveBase
from migrate_stock_replay import create_replay_engine, load_runtime_settings


def _check_signature(expression: str):
    expression = re.sub(r"'([0-9]+(?:\.[0-9]+)?)'::numeric\b", r"\1", expression, flags=re.IGNORECASE)
    expression = re.sub(r"::(?:character varying|text|bpchar|numeric)\b", "", expression, flags=re.IGNORECASE)
    pattern = re.compile(r"'(?:[^']|'')*'|\"[A-Za-z_][A-Za-z_0-9]*\"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+(?:\.[0-9]+)?|<>|!=|<=|>=|\|\||[()=<>+~\-]")
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
    precedence = {"or": 1, "and": 2, "=": 3, "<>": 3, "<": 3, ">": 3, "<=": 3, ">=": 3, "~": 3, "||": 4, "+": 5, "-": 5}

    def parse(minimum=0):
        nonlocal cursor
        if cursor >= len(tokens):
            raise RuntimeError("unsupported schema check predicate")
        token = tokens[cursor]
        cursor += 1
        if token == "(":
            node = parse()
            if cursor >= len(tokens) or tokens[cursor] != ")":
                raise RuntimeError("unsupported schema check grouping")
            cursor += 1
        elif token.startswith("'"):
            node = ("literal", token)
        elif token[0].isdigit():
            node = ("number", Decimal(token))
        elif re.fullmatch(r'"?[a-z_][a-z_0-9]*"?', token) and token not in precedence:
            node = ("column", token.strip('"'))
        else:
            raise RuntimeError("unsupported schema check predicate")
        while cursor < len(tokens) and tokens[cursor] in precedence and precedence[tokens[cursor]] >= minimum:
            operator = tokens[cursor]
            cursor += 1
            right = parse(precedence[operator] + 1)
            if operator in ("and", "or"):
                left_nodes = node[1:] if node[0] == operator else (node,)
                right_nodes = right[1:] if right[0] == operator else (right,)
                node = (operator, *left_nodes, *right_nodes)
            else:
                node = (operator, node, right)
        return node

    signature = parse()
    if cursor != len(tokens):
        raise RuntimeError("unsupported schema check expression")
    return signature


async def validate_schema(connection: AsyncConnection) -> None:
    def validate(sync_connection):
        inspector = inspect(sync_connection)
        dialect = postgresql.dialect()
        for table in ArchiveBase.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                raise RuntimeError(f"missing archive table: {table.name}")
            columns = {column["name"]: column for column in inspector.get_columns(table.name)}
            if set(columns) != set(table.columns.keys()):
                raise RuntimeError(f"schema column mismatch: {table.name}")
            for expected in table.columns:
                actual = columns[expected.name]
                if (actual["nullable"] != expected.nullable
                    or actual["type"].compile(dialect=dialect) != expected.type.compile(dialect=dialect)
                    or actual.get("default") is not None or actual.get("identity") or actual.get("computed")):
                    raise RuntimeError(f"schema type/nullability/default mismatch: {table.name}.{expected.name}")
            if inspector.get_pk_constraint(table.name)["constrained_columns"] != [column.name for column in table.primary_key.columns]:
                raise RuntimeError(f"schema primary key mismatch: {table.name}")
            unique_constraints = inspector.get_unique_constraints(table.name)
            actual_unique = {tuple(value["column_names"]) for value in unique_constraints}
            expected_unique = {tuple(column.name for column in value.columns) for value in table.constraints if isinstance(value, UniqueConstraint)}
            if actual_unique != expected_unique or any(value.get("dialect_options", {}).get("postgresql_nulls_not_distinct") for value in unique_constraints):
                raise RuntimeError(f"schema unique constraint mismatch: {table.name}")
            checks = inspector.get_check_constraints(table.name)
            actual_checks = {value["name"]: value["sqltext"] for value in checks}
            expected_checks = {value.name: str(value.sqltext) for value in table.constraints if isinstance(value, CheckConstraint)}
            if set(actual_checks) != set(expected_checks) or any(value.get("dialect_options", {}).get("not_valid") for value in checks):
                raise RuntimeError(f"schema check constraint mismatch: {table.name}")
            for name, expression in expected_checks.items():
                if _check_signature(actual_checks[name]) != _check_signature(expression):
                    raise RuntimeError(f"schema check expression mismatch: {table.name}.{name}")
            actual_indexes = {value["name"]: value for value in inspector.get_indexes(table.name) if not value.get("duplicates_constraint")}
            if set(actual_indexes) != {value.name for value in table.indexes}:
                raise RuntimeError(f"schema index mismatch: {table.name}")
            for expected in table.indexes:
                actual = actual_indexes[expected.name]
                options = actual.get("dialect_options", {})
                if (tuple(actual["column_names"]) != tuple(column.name for column in expected.columns)
                    or bool(actual.get("unique")) != bool(expected.unique)
                    or actual.get("column_sorting") or actual.get("include_columns")
                    or options.get("postgresql_where") is not None or options.get("postgresql_include")
                    or options.get("postgresql_using", "btree") != "btree" or options.get("postgresql_with")):
                    raise RuntimeError(f"schema index mismatch: {table.name}.{expected.name}")
            actual_foreign = set()
            for value in inspector.get_foreign_keys(table.name):
                options = value.get("options", {})
                if (value.get("referred_schema") not in (None, inspector.default_schema_name)
                    or any(options.get(name) is not None for name in ("onupdate", "deferrable", "initially", "match"))):
                    raise RuntimeError(f"schema foreign key mismatch: {table.name}")
                actual_foreign.add((tuple(value["constrained_columns"]), value["referred_table"], tuple(value["referred_columns"]), options.get("ondelete")))
            expected_foreign = {(tuple(element.parent.name for element in value.elements), value.referred_table.name,
                tuple(element.column.name for element in value.elements), value.ondelete) for value in table.foreign_key_constraints}
            if actual_foreign != expected_foreign:
                raise RuntimeError(f"schema foreign key mismatch: {table.name}")

    await connection.run_sync(validate)


async def migrate_stock_daily_archive(engine: AsyncEngine, *, check: bool = False) -> dict:
    migration_bytes = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(migration_bytes).hexdigest()
    async with engine.begin() as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        await connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": ADVISORY_LOCK_ID})
        if check:
            exists = (await connection.execute(text("SELECT to_regclass('stock_archive_schema_versions')"))).scalar_one_or_none()
            if exists is None:
                raise RuntimeError("stock daily archive migration not applied: missing schema version table")
        else:
            await connection.execute(text("CREATE TABLE IF NOT EXISTS stock_archive_schema_versions (version VARCHAR(64) PRIMARY KEY, checksum CHAR(64) NOT NULL)"))
        existing = (await connection.execute(text("SELECT checksum FROM stock_archive_schema_versions WHERE version = :version"),
            {"version": MIGRATION_VERSION})).scalar_one_or_none()
        if existing is not None and existing != checksum:
            raise RuntimeError("stock daily archive migration checksum mismatch; create a new migration instead of modifying applied SQL")
        if existing is None:
            if check:
                raise RuntimeError("stock daily archive migration not applied")
            for statement in migration_bytes.decode("utf-8").split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await validate_schema(connection)
            await connection.execute(text("INSERT INTO stock_archive_schema_versions (version, checksum) VALUES (:version, :checksum)"),
                {"version": MIGRATION_VERSION, "checksum": checksum})
        else:
            await validate_schema(connection)
    return {"version": MIGRATION_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


async def _main(check: bool) -> None:
    engine = create_replay_engine(load_runtime_settings())
    try:
        print(json.dumps(await migrate_stock_daily_archive(engine, check=check)))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验结构和版本，不创建表")
    args = parser.parse_args()
    try:
        asyncio.run(_main(args.check))
    except Exception as error:
        print(f"stock daily archive migration failed ({type(error).__name__}); no partial migration committed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
