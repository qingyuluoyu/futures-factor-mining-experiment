import hashlib
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, text
from sqlalchemy.dialects import postgresql

from app.models.stock_eod_recovery import StockEodRecovery
from scripts.migrate_stock_research_jobs import _check_signature


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations/20261020_007_stock_eod_recovery.sql"
VERSION = "20261020_007_stock_eod_recovery"


async def validate_schema(connection):
    def validate(sync):
        inspector = inspect(sync)
        table = StockEodRecovery.__table__
        columns = {item["name"]: item for item in inspector.get_columns(table.name)}
        if set(columns) != set(table.columns.keys()):
            raise RuntimeError("EOD recovery columns mismatch")
        for column in table.columns:
            actual = columns[column.name]
            if str(actual["type"].compile(dialect=postgresql.dialect())) != str(column.type.compile(dialect=postgresql.dialect())) or actual["nullable"] != column.nullable or actual.get("default") is not None:
                raise RuntimeError("EOD recovery column contract mismatch")
        if inspector.get_pk_constraint(table.name)["constrained_columns"] != ["id"]:
            raise RuntimeError("EOD recovery primary key mismatch")
        expected_unique = {tuple(column.name for column in constraint.columns) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
        if {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name)} != expected_unique:
            raise RuntimeError("EOD recovery unique constraints mismatch")
        checks = {item["name"]: _check_signature(item["sqltext"]) for item in inspector.get_check_constraints(table.name)}
        if checks != {item.name: _check_signature(str(item.sqltext)) for item in table.constraints if isinstance(item, CheckConstraint)}:
            raise RuntimeError("EOD recovery check constraints mismatch")
        if inspector.get_foreign_keys(table.name):
            raise RuntimeError("EOD recovery unexpected foreign keys")
    await connection.run_sync(validate)


async def migrate_stock_eod_recovery(connection, *, check=False):
    await connection.execute(text("SET LOCAL lock_timeout = '3s'"))
    await connection.execute(text("SET LOCAL statement_timeout = '15s'"))
    await connection.execute(text("SELECT pg_advisory_xact_lock(20261020007)"))
    baseline = await connection.scalar(text("SELECT checksum FROM stock_eod_schema_versions WHERE version='20261018_003_stock_eod'"))
    if baseline != hashlib.sha256((ROOT / "migrations/20261018_003_stock_eod.sql").read_bytes()).hexdigest():
        raise RuntimeError("EOD baseline migration missing or changed")
    source = MIGRATION_PATH.read_bytes()
    checksum = hashlib.sha256(source).hexdigest()
    existing = await connection.scalar(text("SELECT checksum FROM stock_eod_schema_versions WHERE version=:version"), {"version": VERSION})
    if existing is not None and existing != checksum:
        raise RuntimeError("EOD recovery migration checksum mismatch")
    if existing is None:
        if check:
            raise RuntimeError("EOD recovery migration not applied")
        for statement in source.decode("utf-8").split(";"):
            if statement.strip():
                await connection.execute(text(statement))
        await connection.execute(text("INSERT INTO stock_eod_schema_versions(version,checksum) VALUES (:version,:checksum)"), {"version": VERSION, "checksum": checksum})
    await validate_schema(connection)
    return {"version": VERSION, "checksum": checksum, "applied": existing is None, "checked": True}
