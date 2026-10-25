import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql

from app.models.gp_checkpoint import GpCheckpointBase
from scripts.migrate_stock_research_jobs import _check_signature


VERSION = "20261020_008_gp_checkpoint"
MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / f"{VERSION}.sql"


async def validate_schema(connection):
    def validate(sync):
        inspector = inspect(sync)
        for table in GpCheckpointBase.metadata.sorted_tables:
            columns = {item["name"]: item for item in inspector.get_columns(table.name)}
            if set(columns) != set(table.columns.keys()):
                raise RuntimeError("GP checkpoint columns mismatch")
            for column in table.columns:
                actual = columns[column.name]
                if str(actual["type"].compile(dialect=postgresql.dialect())) != str(column.type.compile(dialect=postgresql.dialect())) or actual["nullable"] != column.nullable or actual.get("default") is not None:
                    raise RuntimeError("GP checkpoint column contract mismatch")
            if inspector.get_pk_constraint(table.name)["constrained_columns"] != [column.name for column in table.primary_key]:
                raise RuntimeError("GP checkpoint primary key mismatch")
            uniques = {tuple(column.name for column in constraint.columns) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
            if {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)} != uniques:
                raise RuntimeError("GP checkpoint unique constraints mismatch")
            checks = {item["name"]: _check_signature(item["sqltext"]) for item in inspector.get_check_constraints(table.name)}
            if checks != {item.name: _check_signature(str(item.sqltext)) for item in table.constraints if isinstance(item, CheckConstraint)} or inspector.get_foreign_keys(table.name):
                raise RuntimeError("GP checkpoint constraint mismatch")
    await connection.run_sync(validate)


async def migrate_gp_checkpoint(connection, *, check=False):
    await connection.execute(text("SET LOCAL lock_timeout = '3s'"))
    await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    await connection.execute(text("SELECT pg_advisory_xact_lock(20261020008)"))
    source = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(source).hexdigest()
    exists = await connection.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.gp_checkpoint_schema_versions')"))
    existing = await connection.scalar(text("SELECT checksum FROM gp_checkpoint_schema_versions WHERE version=:version"), {"version": VERSION}) if exists else None
    if existing is not None and existing != checksum:
        raise RuntimeError("GP checkpoint migration checksum mismatch")
    if existing is None:
        if check:
            raise RuntimeError("GP checkpoint migration not applied")
        for statement in source.decode("utf-8").split(";"):
            if statement.strip():
                await connection.execute(text(statement))
        await connection.execute(text("INSERT INTO gp_checkpoint_schema_versions(version,checksum,applied_at) VALUES (:version,:checksum,:now)"), {"version": VERSION, "checksum": checksum, "now": datetime.now(timezone.utc)})
    await validate_schema(connection)
    return {"version": VERSION, "checksum": checksum, "applied": existing is None, "checked": True}
