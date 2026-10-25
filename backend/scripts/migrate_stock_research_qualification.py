"""Additive relaxation of the stock research dataset qualification grade (migration 020).

Follows the established runner contract: read-only check by default, advisory lock,
5s lock timeout, explicit statement timeout, SQL split on ';', allowlisted DDL,
reflected-schema validation and a registered checksum row. A missing version row
must refuse service; nothing is auto-created or re-collected at startup.
"""
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
from app.models.stock_research_dataset import StockResearchDataset
from app.services.stock_research_v2.qualification import expected_grade, grade_allowance
from migrate_stock_replay import create_replay_engine, load_runtime_settings

SCHEMA_VERSION = "20261025_020_stock_research_qualification"
SCHEMA_PATH = BACKEND_ROOT / "migrations" / f"{SCHEMA_VERSION}.sql"
VERSION_TABLE = "stock_research_qualification_schema_versions"
GRADE_CHECK = "ck_stock_dataset_qualification"
GRADE_COLUMN = "qualification"
ADVISORY_LOCK = 20261025020
PARENT_VERSION = "20261023_015_stock_research_datasets"
PARENT_PATH = BACKEND_ROOT / "migrations" / f"{PARENT_VERSION}.sql"


class MigrationError(RuntimeError):
    pass


def migration_statements(sql=None):
    """Allowlist for the fixed 020 DDL, not a general-purpose SQL parser."""
    source = SCHEMA_PATH if sql is None else sql
    statements = [part.strip() for part in re.sub(r"--[^\n]*", "", source.read_text(encoding="utf8") if isinstance(source, Path) else source).split(";") if part.strip()]
    allowed = (
        r"ALTER\s+TABLE\s+stock_research_datasets\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+" + GRADE_CHECK,
        r"ALTER\s+TABLE\s+stock_research_datasets\s+ADD\s+CONSTRAINT\s+" + GRADE_CHECK + r"\s+CHECK\s*\(",
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+" + VERSION_TABLE + r"\s*\(",
    )
    if not statements or any(not any(re.match(pattern, statement, re.IGNORECASE) for pattern in allowed) for statement in statements):
        raise MigrationError("stock qualification migration contains unsupported DDL")
    if sum(statement.upper().startswith("ALTER TABLE") for statement in statements) != 2:
        raise MigrationError("stock qualification migration must relax exactly one named constraint")
    return statements


def canonical_grade(expression):
    """Reduce either dialect spelling of the grade CHECK to one canonical set."""
    try:
        return grade_allowance(expression)
    except ValueError as error:
        raise MigrationError(str(error)) from None


def expected_grade_reference():
    try:
        return expected_grade()
    except ValueError as error:
        raise MigrationError(str(error)) from None


def _table_exists(connection, name):
    if connection.dialect.name == "postgresql":
        return connection.execute(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || '" + name + "')")).scalar() is not None
    return connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='" + name + "'")).scalar() == 1


def validate_schema(connection):
    """Name + canonical-text comparison of the relaxed grade check against the model."""
    if not _table_exists(connection, "stock_research_datasets"):
        raise MigrationError("stock dataset migration 015 required first")
    if not _table_exists(connection, VERSION_TABLE):
        raise MigrationError("stock qualification migration not applied")
    schema = connection.execute(text("SELECT current_schema()")).scalar() if connection.dialect.name == "postgresql" else None

    def validate(sync_connection):
        inspector = inspect(sync_connection)
        table = StockResearchDataset.__table__
        if not inspector.has_table(table.name, schema=schema):
            raise MigrationError("stock dataset table missing")
        actual = {item["name"]: item for item in inspector.get_columns(table.name, schema=schema)}
        if set(actual) != set(table.columns.keys()):
            raise MigrationError("stock dataset columns mismatch")
        for column in table.columns:
            found = actual[column.name]
            if found["nullable"] != column.nullable:
                raise MigrationError("stock dataset column nullability mismatch")
            # Exact DDL types are a PostgreSQL deployment guarantee; other dialects
            # reflect UUID/TIMESTAMPTZ as their local storage type.
            if connection.dialect.name == "postgresql" and found["type"].compile(dialect=postgresql.dialect()) != column.type.compile(dialect=postgresql.dialect()):
                raise MigrationError("stock dataset column type mismatch")
        reflected = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(table.name, schema=schema)}
        if GRADE_CHECK not in reflected:
            raise MigrationError("stock qualification grade check missing")
        if canonical_grade(reflected[GRADE_CHECK]) != expected_grade_reference():
            raise MigrationError("stock qualification grade check mismatch")

    validate(connection)


def _transaction(connection, check):
    """One explicit transaction; every prerequisite failure rolls the whole thing back."""
    dialect = connection.dialect.name
    if check and dialect == "postgresql":
        connection.execute(text("SET TRANSACTION READ ONLY"))
    if dialect == "postgresql":
        connection.execute(text("SET LOCAL lock_timeout='5s'"))
        connection.execute(text("SET LOCAL statement_timeout='30s'"))
    if not _table_exists(connection, "users"):
        raise MigrationError("users migration required first")
    if not _table_exists(connection, "stock_research_datasets"):
        raise MigrationError("stock dataset migration 015 required first")
    parent_checksum = None
    if _table_exists(connection, "stock_research_dataset_schema_versions"):
        parent_checksum = connection.execute(text("SELECT checksum FROM stock_research_dataset_schema_versions WHERE version='" + PARENT_VERSION + "'")).scalar()
    if parent_checksum != hashlib.sha256(PARENT_PATH.read_bytes()).hexdigest():
        raise MigrationError("verified stock dataset migration 015 required first")
    if not check and dialect == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(" + str(ADVISORY_LOCK) + ")"))
    if not check:
        connection.execute(text(f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
    if not _table_exists(connection, VERSION_TABLE):
        raise MigrationError("stock qualification migration not applied")
    existing = connection.execute(text("SELECT checksum FROM " + VERSION_TABLE + " WHERE version='" + SCHEMA_VERSION + "'")).scalar()
    if existing is not None and existing != checksum:
        raise MigrationError("stock qualification migration checksum mismatch")
    if existing is None:
        if check:
            raise MigrationError("stock qualification migration not applied")
        for statement in migration_statements():
            connection.execute(text(statement))
        validate_schema(connection)
        connection.execute(text("INSERT INTO " + VERSION_TABLE + "(version,checksum) VALUES ('" + SCHEMA_VERSION + "','" + checksum + "')"))
    else:
        validate_schema(connection)
    return {"version": SCHEMA_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


checksum = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


async def migrate_stock_research_qualification(engine, *, check=True):
    """Accept the deployment AsyncEngine or a plain Engine; same fail-closed contract."""
    if hasattr(engine, "run_sync"):
        return await engine.run_sync(_transaction, check)
    with engine.begin() as connection:
        return _transaction(connection, check)


async def run(check):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost", "::1") or settings.postgres_port != 5433:
        raise MigrationError("dedicated loopback PG5433 required")
    engine = create_replay_engine(settings)
    try:
        print(json.dumps(await migrate_stock_research_qualification(engine, check=check)))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Additive stock research qualification grade registry; defaults to read-only check")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(not arguments.apply))
    except Exception as error:
        reason = str(error) if isinstance(error, MigrationError) else type(error).__name__
        print(f"stock qualification migration failed: {reason}; transaction rolled back", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
