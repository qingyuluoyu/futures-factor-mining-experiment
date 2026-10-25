import hashlib
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError

from tests.test_stock_archive_jobs_migration import Connection, Engine, Inspector, ReflectedConnection


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_stock_archive_recovery.py"
    assert path.exists(), "archive recovery migration entry point is missing"
    spec = spec_from_file_location("stock_archive_recovery_migration_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_first_repeat_check_locked_bounded_and_atomic(monkeypatch):
    module = migration_module()
    connection, validate = Connection(), AsyncMock()
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", validate)
    assert await module.migrate_stock_archive_recovery(engine) == {
        "version": "20261019_006_stock_archive_recovery",
        "checksum": hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest(),
        "applied": True, "checked": True,
    }
    assert connection.statements[:3] == [
        ("SET LOCAL lock_timeout = '5s'", None),
        ("SET LOCAL statement_timeout = '30s'", None),
        ("SELECT pg_advisory_xact_lock(:lock_id)", {"lock_id": 20261019006}),
    ]
    connection.checksum = hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest()
    assert (await module.migrate_stock_archive_recovery(engine))["applied"] is False
    checkpoint = len(connection.statements)
    assert (await module.migrate_stock_archive_recovery(engine, check=True))["applied"] is False
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in connection.statements[checkpoint:])
    assert sum("CREATE TABLE stock_archive_recoveries" in sql for sql, _ in connection.statements) == 1
    assert validate.await_count == 3 and engine.commits == 3


@pytest.mark.parametrize("exists,checksum,error", [(False, None, "not applied"), (True, None, "not applied"), (True, "changed", "checksum")])
async def test_check_missing_or_changed_version_never_writes(monkeypatch, exists, checksum, error):
    module = migration_module()
    engine = Engine(Connection(checksum, exists))
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match=error):
        await module.migrate_stock_archive_recovery(engine, check=True)
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in engine.connection.statements)
    assert engine.rollbacks == 1 and engine.commits == 0


@pytest.mark.parametrize("failure", ["schema", "index"])
async def test_failure_rolls_back_ddl_and_version(monkeypatch, failure):
    module = migration_module()
    engine = Engine(Connection(fail_at="CREATE INDEX" if failure == "index" else None))
    monkeypatch.setattr(module, "validate_schema", AsyncMock(side_effect=RuntimeError("schema mismatch") if failure == "schema" else None))
    with pytest.raises(RuntimeError):
        await module.migrate_stock_archive_recovery(engine)
    assert engine.rollbacks == 1 and engine.commits == 0
    assert engine.connection.checksum is None and engine.connection.ddl == []
    assert not any("INSERT INTO" in sql for sql, _ in engine.connection.statements)


async def test_reflection_accepts_independent_tables(monkeypatch):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.RecoveryBase.metadata))
    await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("table,change", [
    ("stock_archive_recoveries", name) for name in ("table", "columns", "nullability", "type", "default", "pk", "unique", "extra_unique", "deferrable_unique", "check", "check_expression", "not_valid", "index", "index_order", "unique_index", "partial_index", "extra_index", "extra_fk")
] + [("stock_archive_recovery_schema_versions", name) for name in ("table", "columns", "nullability", "type", "default", "pk", "extra_fk", "extra_index")])
async def test_reflection_rejects_schema_drift(monkeypatch, table, change):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.RecoveryBase.metadata, (table, change)))
    with pytest.raises(RuntimeError, match="missing|mismatch"):
        await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("change", [("relkind", "v"), ("relpersistence", "u"), ("unsafe_constraints", True), ("unsafe_indexes", True)])
async def test_catalog_rejects_unsafe_constraints_indexes_and_relations(monkeypatch, change):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.RecoveryBase.metadata))
    with pytest.raises(RuntimeError, match="mismatch"):
        await module.validate_schema(ReflectedConnection(change))


def test_isolated_model_contract():
    module = migration_module()
    from app.models.base import Base
    from app.models.stock_daily_archive import ArchiveBase
    from app.models.stock_archive_job import ArchiveJobBase
    from app.models.stock_archive_recovery import StockArchiveRecovery, StockArchiveRecoveryVersion

    assert all(module.RecoveryBase.metadata is not base.metadata for base in (Base, ArchiveBase, ArchiveJobBase))
    assert set(module.RecoveryBase.metadata.tables) == {"stock_archive_recoveries", "stock_archive_recovery_schema_versions"}
    assert StockArchiveRecoveryVersion.__tablename__ == "stock_archive_recovery_schema_versions"
    table = StockArchiveRecovery.__table__
    assert set(table.columns.keys()) == {"id", "source_job_id", "source_batch_id", "source_data_version", "method_version", "operator_id", "idempotency_key", "request_hash", "request_id", "reason", "attempt_number", "status", "step", "started_at", "updated_at", "deadline_at", "finished_at", "archive_batch_id", "error_code", "error_message"}
    assert {column.name for column in table.columns if column.nullable} == {"finished_at", "archive_batch_id", "error_code", "error_message"}
    assert all(column.server_default is None for column in table.columns)
    assert not table.foreign_key_constraints
    assert {tuple(column.name for column in value.columns) for value in table.constraints if isinstance(value, UniqueConstraint)} == {("operator_id", "idempotency_key"), ("source_job_id", "attempt_number")}
    assert {tuple(column.name for column in value.columns) for value in table.indexes} == {("status", "deadline_at"), ("source_job_id", "started_at")}
    assert all(module._check_signature(str(constraint.sqltext)) for constraint in table.constraints if isinstance(constraint, CheckConstraint))


def recovery_values(**changes):
    now = datetime.now(timezone.utc)
    values = dict(id=uuid4(), source_job_id=uuid4(), source_batch_id=uuid4(), source_data_version="a" * 64,
        method_version="stock_daily_archive_v1", operator_id=uuid4(), idempotency_key=uuid4(), request_hash="b" * 64,
        request_id="c" * 32, reason="controlled recovery", attempt_number=1, status="running", step="recovering",
        started_at=now, updated_at=now, deadline_at=now + timedelta(seconds=90),
        finished_at=None, archive_batch_id=None, error_code=None, error_message=None)
    values.update(changes)
    return values


@pytest.mark.parametrize("changes", [
    {"attempt_number": 0}, {"attempt_number": 3}, {"status": "pending"}, {"step": "archiving"},
    {"finished_at": datetime.now(timezone.utc)}, {"archive_batch_id": uuid4()}, {"error_code": "error"},
    {"error_message": "error"}, {"status": "completed", "step": "completed"},
    {"status": "failed", "step": "failed"}, {"status": "cancelled", "step": "cancelled"},
    {"deadline_at": datetime(2000, 1, 1)}, {"updated_at": datetime(2000, 1, 1)},
    {"status": "failed", "step": "failed", "finished_at": datetime(2000, 1, 1), "error_code": "error"},
    {"status": "completed", "step": "completed", "finished_at": datetime(2100, 1, 1), "archive_batch_id": uuid4(), "error_code": "error"},
    {"status": "completed", "step": "completed", "finished_at": datetime(2100, 1, 1), "archive_batch_id": uuid4(), "error_message": "error"},
    {"status": "failed", "step": "failed", "finished_at": datetime(2100, 1, 1), "archive_batch_id": uuid4(), "error_code": "error"},
])
def test_real_constraints_reject_invalid_state(changes):
    module = migration_module()
    engine = create_engine("sqlite://")
    module.RecoveryBase.metadata.create_all(engine)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(module.RecoveryBase.metadata.tables["stock_archive_recoveries"].insert(), recovery_values(**changes))
    engine.dispose()


@pytest.mark.parametrize("status", ["running", "completed", "failed", "cancelled"])
def test_real_constraints_accept_valid_lifecycle_and_bounded_attempts(status):
    module = migration_module()
    engine = create_engine("sqlite://")
    module.RecoveryBase.metadata.create_all(engine)
    values = recovery_values(status=status, step="recovering" if status == "running" else status, attempt_number=2)
    if status != "running":
        values["finished_at"] = values["started_at"]
        if status == "completed":
            values["archive_batch_id"] = uuid4()
        else:
            values["error_code"] = "recovery_failed"
    with engine.begin() as connection:
        connection.execute(module.RecoveryBase.metadata.tables["stock_archive_recoveries"].insert(), values)
    engine.dispose()


@pytest.mark.parametrize("key_fields", [("operator_id", "idempotency_key"), ("source_job_id", "attempt_number")])
def test_real_unique_constraints_prevent_idempotency_reuse_and_duplicate_attempt(key_fields):
    module = migration_module()
    engine = create_engine("sqlite://")
    module.RecoveryBase.metadata.create_all(engine)
    table = module.RecoveryBase.metadata.tables["stock_archive_recoveries"]
    original = recovery_values()
    duplicate = recovery_values(**{name: original[name] for name in key_fields})
    with engine.begin() as connection:
        connection.execute(table.insert(), original)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(table.insert(), duplicate)
    engine.dispose()


def test_sql_additive_and_cli_redacts_errors(monkeypatch, capsys):
    module = migration_module()
    sql = module.MIGRATION_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE stock_archive_recoveries" in sql
    assert "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper() and "REFERENCES" not in sql.upper()
    monkeypatch.setattr(module, "_main", AsyncMock(side_effect=RuntimeError("postgresql://user:secret@private-host")))
    monkeypatch.setattr(module.sys, "argv", ["migrate_stock_archive_recovery.py", "--check"])
    assert module.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and "secret" not in captured.err and "private-host" not in captured.err
    assert captured.out == ""


async def test_cli_disposes_engine_on_failure(monkeypatch):
    module = migration_module()
    engine = type("DisposableEngine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(module, "load_runtime_settings", lambda: object())
    monkeypatch.setattr(module, "create_replay_engine", lambda _: engine)
    monkeypatch.setattr(module, "migrate_stock_archive_recovery", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await module._main(True)
    engine.dispose.assert_awaited_once()
