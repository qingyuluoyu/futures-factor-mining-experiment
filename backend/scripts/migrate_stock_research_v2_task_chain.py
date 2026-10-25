"""Additive widening of the stock research v2 task chain (migration 021).

Follows the established runner contract: read-only check by default, advisory lock,
5s lock timeout, explicit statement timeout, SQL split on ';', allowlisted DDL,
reflected-schema validation and a registered checksum row. A missing version row
must refuse service; nothing is auto-created or re-collected at startup.

021 only *references* the 019 registry tables through the new result foreign key;
it never rewrites 015/016/017/018/019 DDL, version rows or checksums.
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
from app.models.stock_research_v2_job import V2JobBase
from migrate_stock_research_datasets import normalize_check
from migrate_stock_replay import create_replay_engine, load_runtime_settings

SCHEMA_VERSION = "20261025_021_stock_research_v2_task_chain"
SCHEMA_PATH = BACKEND_ROOT / "migrations" / f"{SCHEMA_VERSION}.sql"
VERSION_TABLE = "stock_research_v2_task_chain_schema_versions"
JOB_TABLES = ("stock_research_v2_jobs", "stock_research_v2_workers")
ADVISORY_LOCK = 20261025021
# 021 only relaxes these four named constraints; every other 016 constraint is
# re-asserted verbatim by validate_schema so a drift cannot pass as a relaxation.
RELAXED_CHECKS = ("ck_stock_v2_job_kind", "ck_stock_v2_job_result", "ck_stock_v2_job_runtime", "ck_stock_v2_worker_name")
# (table that must exist, version row that must already be registered)
PARENTS = (("users", None, None, None),
    ("stock_research_datasets", "20261023_015_stock_research_datasets",
        "stock_research_dataset_schema_versions", "20261023_015_stock_research_datasets.sql"),
    ("stock_research_v2_job_schema_versions", "20261023_016_stock_research_v2_jobs",
        "stock_research_v2_job_schema_versions", "20261023_016_stock_research_v2_jobs.sql"),
    ("stock_ml_registry_schema_versions", "20261025_019_stock_ml_registry",
        "stock_ml_registry_schema_versions", "20261025_019_stock_ml_registry.sql"))


class MigrationError(RuntimeError):
    pass


def migration_statements(sql=None):
    """Allowlist for the fixed 021 DDL, not a general-purpose SQL parser."""
    source = SCHEMA_PATH if sql is None else sql
    text_source = source.read_text(encoding="utf8") if isinstance(source, Path) else sql
    statements = [part.strip() for part in re.sub(r"--[^\n]*", "", text_source).split(";") if part.strip()]
    allowed = (
        r"ALTER\s+TABLE\s+stock_research_v2_jobs\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+ck_stock_v2_job_"
        + "(?:kind|runtime|result)",
        r"ALTER\s+TABLE\s+stock_research_v2_jobs\s+ADD\s+CONSTRAINT\s+ck_stock_v2_job_(?:kind|runtime|result)\s+CHECK\s*\(",
        r"ALTER\s+TABLE\s+stock_research_v2_jobs\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+result_"
        + "(?:experiment_id|report_sha256|artifact_id)",
        r"ALTER\s+TABLE\s+stock_research_v2_workers\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+ck_stock_v2_worker_name",
        r"ALTER\s+TABLE\s+stock_research_v2_workers\s+ADD\s+CONSTRAINT\s+ck_stock_v2_worker_name\s+CHECK\s*\(",
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+" + VERSION_TABLE + r"\s*\(",
    )
    if not statements or any(not any(re.match(pattern, statement, re.IGNORECASE) for pattern in allowed) for statement in statements):
        raise MigrationError("stock v2 task chain migration contains unsupported DDL")
    altered = [statement for statement in statements if statement.upper().startswith("ALTER TABLE")]
    if len(altered) != 11 or sum("DROP CONSTRAINT IF EXISTS" in statement for statement in statements) != 4:
        raise MigrationError("stock v2 task chain migration must relax exactly four named constraints")
    if sum("ADD CONSTRAINT" in statement for statement in statements) != 4 or sum("ADD COLUMN IF NOT EXISTS" in statement for statement in statements) != 3:
        raise MigrationError("stock v2 task chain migration must add exactly three nullable result columns")
    return statements


def _table_exists(connection, name):
    if connection.dialect.name == "postgresql":
        return connection.execute(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || '" + name + "')")).scalar() is not None
    return connection.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='" + name + "'")).scalar() == 1


def validate_schema(connection):
    """Name + canonical-text comparison of every 016 constraint against the model.

    The four relaxed constraints are compared first so a legacy single-kind or
    single-result CHECK is reported as the constraint problem it is, not as a
    column problem; every other constraint is then re-asserted verbatim.
    """
    if not _table_exists(connection, "stock_research_v2_jobs"):
        raise MigrationError("v2 job migration 016 required first")
    if not _table_exists(connection, VERSION_TABLE):
        raise MigrationError("stock v2 task chain migration not applied")
    schema = connection.execute(text("SELECT current_schema()")).scalar() if connection.dialect.name == "postgresql" else None
    inspector = inspect(connection)
    reflected = {table.name: {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(table.name, schema=schema)}
        for table in V2JobBase.metadata.sorted_tables}
    expected = {item.name: normalize_check(str(item.sqltext)) for item in _constraints() if hasattr(item, "sqltext")}
    for name in RELAXED_CHECKS:
        found = next((value for table in reflected for key, value in reflected[table].items() if key == name), None)
        if found is None or normalize_check(found) != expected[name]:
            raise MigrationError("v2 job check mismatch: " + name)

    def validate(sync_connection):
        for table in V2JobBase.metadata.sorted_tables:
            if not inspector.has_table(table.name, schema=schema):
                raise MigrationError("v2 job table missing")
            actual = {item["name"]: item for item in inspector.get_columns(table.name, schema=schema)}
            if set(actual) != set(table.columns.keys()):
                raise MigrationError("v2 job columns mismatch")
            for column in table.columns:
                found = actual[column.name]
                if found["nullable"] != column.nullable:
                    raise MigrationError("v2 job column nullability mismatch")
                if connection.dialect.name == "postgresql" and found["type"].compile(dialect=postgresql.dialect()) != column.type.compile(dialect=postgresql.dialect()):
                    raise MigrationError("v2 job column type mismatch")
                default = str(found.get("default") or "").lower().replace(" ", "")
                if (column.server_default is not None and default not in {"current_timestamp", "now()", "transaction_timestamp()"}) or (column.server_default is None and default):
                    raise MigrationError("v2 job default mismatch")
            if inspector.get_pk_constraint(table.name, schema=schema)["constrained_columns"] != [item.name for item in table.primary_key.columns]:
                raise MigrationError("v2 job primary key mismatch")
            expected_checks = {item.name: normalize_check(str(item.sqltext)) for item in table.constraints if hasattr(item, "sqltext")}
            if {key: normalize_check(value) for key, value in reflected[table.name].items()} != expected_checks:
                raise MigrationError("v2 job check mismatch")
            expected_unique = {item.name: [column.name for column in item.columns] for item in table.constraints if item.__class__.__name__ == "UniqueConstraint"}
            actual_unique = {item["name"]: item["column_names"] for item in inspector.get_unique_constraints(table.name, schema=schema)}
            if actual_unique != expected_unique:
                raise MigrationError("v2 job uniqueness mismatch")
            expected_foreign = {(tuple(item.column_keys), next(iter(item.elements)).column.table.name, tuple(part.column.name for part in item.elements), item.ondelete) for item in table.foreign_key_constraints}
            actual_foreign = set()
            for item in inspector.get_foreign_keys(table.name, schema=schema):
                if item.get("referred_schema") not in (None, schema):
                    raise MigrationError("v2 job reference schema mismatch")
                actual_foreign.add((tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]), item.get("options", {}).get("ondelete")))
            if actual_foreign != expected_foreign:
                raise MigrationError("v2 job reference mismatch")
            expected_indexes = {item.name: ([part.name for part in item.columns], item.unique) for item in table.indexes}
            actual_indexes = {}
            for item in inspector.get_indexes(table.name, schema=schema):
                if item.get("duplicates_constraint"):
                    continue
                if item.get("dialect_options", {}).get("postgresql_where"):
                    raise MigrationError("v2 job partial index mismatch")
                actual_indexes[item["name"]] = (item["column_names"], bool(item["unique"]))
            if actual_indexes != expected_indexes:
                raise MigrationError("v2 job index mismatch")

    validate(connection)


def _constraints():
    for table in V2JobBase.metadata.sorted_tables:
        yield from table.constraints


def _transaction(connection, check):
    """One explicit transaction; every prerequisite failure rolls the whole thing back."""
    dialect = connection.dialect.name
    if check and dialect == "postgresql":
        connection.execute(text("SET TRANSACTION READ ONLY"))
    if dialect == "postgresql":
        connection.execute(text("SET LOCAL lock_timeout='5s'"))
        connection.execute(text("SET LOCAL statement_timeout='30s'"))
    for name, *_ in PARENTS:
        if not _table_exists(connection, name):
            raise MigrationError("users, 015 dataset, 016 v2 job and 019 ml registry migrations required first")
    for _, version, table, filename in PARENTS:
        if version is None:
            continue
        registered = connection.execute(text("SELECT checksum FROM " + table + " WHERE version='" + version + "'")).scalar()
        if registered != hashlib.sha256((BACKEND_ROOT / "migrations" / filename).read_bytes()).hexdigest():
            raise MigrationError("verified parent migration " + version + " required first")
    if not check and dialect == "postgresql":
        connection.execute(text(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK})"))
    if not check:
        connection.execute(text(f"CREATE TABLE IF NOT EXISTS {VERSION_TABLE} (version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
    if not _table_exists(connection, VERSION_TABLE):
        raise MigrationError("stock v2 task chain migration not applied")
    existing = connection.execute(text("SELECT checksum FROM " + VERSION_TABLE + " WHERE version='" + SCHEMA_VERSION + "'")).scalar()
    if existing is not None and existing != checksum:
        raise MigrationError("stock v2 task chain migration checksum mismatch")
    if existing is None:
        if check:
            raise MigrationError("stock v2 task chain migration not applied")
        for statement in migration_statements():
            connection.execute(text(statement))
        validate_schema(connection)
        connection.execute(text("INSERT INTO " + VERSION_TABLE + "(version,checksum) VALUES ('" + SCHEMA_VERSION + "','" + checksum + "')"))
    else:
        validate_schema(connection)
    if dialect == "postgresql":
        unvalidated = connection.execute(text("SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema() AND t.relname IN ('"
            + "','".join(JOB_TABLES) + "') AND NOT c.convalidated")).scalar()
        if unvalidated:
            raise MigrationError("v2 job constraints not validated")
    return {"version": SCHEMA_VERSION, "checksum": checksum, "applied": existing is None, "checked": True}


checksum = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


async def migrate_stock_research_v2_task_chain(engine, *, check=True):
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
        print(json.dumps(await migrate_stock_research_v2_task_chain(engine, check=check)))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Additive stock v2 task chain registry; defaults to read-only check")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(run(not arguments.apply))
    except Exception as error:
        reason = str(error) if isinstance(error, MigrationError) else type(error).__name__
        print(f"stock v2 task chain migration failed: {reason}; transaction rolled back", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
