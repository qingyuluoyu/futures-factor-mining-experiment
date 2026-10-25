import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql

from app.models.gp_job import GpJobBase
from scripts.migrate_gp_checkpoint import migrate_gp_checkpoint
from scripts.migrate_stock_research_jobs import _check_signature


VERSION = "20261020_009_gp_jobs"
MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / f"{VERSION}.sql"


def check_signature(expression):
    return _check_signature(re.sub(r"::double precision\b", "", expression, flags=re.IGNORECASE))


async def validate_schema(connection):
    def validate(sync):
        inspector = inspect(sync)
        for table in GpJobBase.metadata.sorted_tables:
            columns = {item["name"]: item for item in inspector.get_columns(table.name)}
            if set(columns) != set(table.columns.keys()):
                raise RuntimeError("GP job columns mismatch")
            for column in table.columns:
                actual = columns[column.name]
                if str(actual["type"].compile(dialect=postgresql.dialect())) != str(column.type.compile(dialect=postgresql.dialect())) or actual["nullable"] != column.nullable or actual.get("default") is not None:
                    raise RuntimeError("GP job column contract mismatch")
            if inspector.get_pk_constraint(table.name)["constrained_columns"] != [column.name for column in table.primary_key]:
                raise RuntimeError("GP job primary key mismatch")
            uniques = {tuple(column.name for column in constraint.columns) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
            if {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)} != uniques:
                raise RuntimeError("GP job unique constraints mismatch")
            checks = {item["name"]: check_signature(item["sqltext"]) for item in inspector.get_check_constraints(table.name)}
            if checks != {item.name: check_signature(str(item.sqltext)) for item in table.constraints if isinstance(item, CheckConstraint)}:
                raise RuntimeError("GP job check constraints mismatch")
            actual_foreign = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]), item.get("options", {}).get("ondelete")) for item in inspector.get_foreign_keys(table.name)}
            expected_foreign = {(tuple(item.parent.name for item in constraint.elements), constraint.referred_table.name, tuple(item.column.name for item in constraint.elements), constraint.ondelete) for constraint in table.foreign_key_constraints}
            if actual_foreign != expected_foreign:
                raise RuntimeError("GP job foreign key mismatch")
            actual_indexes = {(item["name"], tuple(item["column_names"]), bool(item["unique"])) for item in inspector.get_indexes(table.name) if not item.get("duplicates_constraint")}
            if actual_indexes != {(index.name, tuple(column.name for column in index.columns), bool(index.unique)) for index in table.indexes}:
                raise RuntimeError("GP job indexes mismatch")
    await connection.run_sync(validate)


async def migrate_gp_jobs(connection, *, check=False):
    await migrate_gp_checkpoint(connection, check=True)
    await connection.execute(text("SET LOCAL lock_timeout = '3s'"))
    await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    await connection.execute(text("SELECT pg_advisory_xact_lock(20261020009)"))
    source = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(source).hexdigest()
    exists = await connection.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.gp_job_schema_versions')"))
    existing = await connection.scalar(text("SELECT checksum FROM gp_job_schema_versions WHERE version=:version"), {"version": VERSION}) if exists else None
    if existing is not None and existing != checksum:
        raise RuntimeError("GP job migration checksum mismatch")
    if existing is None:
        if check:
            raise RuntimeError("GP job migration not applied")
        for statement in source.decode("utf-8").split(";"):
            if statement.strip():
                await connection.execute(text(statement))
        await connection.execute(text("INSERT INTO gp_job_schema_versions(version,checksum,applied_at) VALUES (:version,:checksum,:now)"), {"version": VERSION, "checksum": checksum, "now": datetime.now(timezone.utc)})
    await validate_schema(connection)
    return {"version": VERSION, "checksum": checksum, "applied": existing is None, "checked": True}
