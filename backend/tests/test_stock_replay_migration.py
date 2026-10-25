import hashlib
from contextlib import asynccontextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_stock_replay.py"
    spec = spec_from_file_location("stock_replay_migration_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Connection:
    def __init__(self, checksum=None, exists=True):
        self.checksum = checksum
        self.exists = exists
        self.statements = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "to_regclass" in sql:
            return Result("stock_replay_schema_versions" if self.exists else None)
        if "SELECT checksum" in sql:
            return Result(self.checksum)
        if "INSERT INTO stock_replay_schema_versions" in sql:
            self.checksum = params["checksum"]
        return Result()


class Engine:
    def __init__(self, connection):
        self.connection = connection
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def begin(self):
        checksum_before = self.connection.checksum
        try:
            yield self.connection
            self.commits += 1
        except Exception:
            self.connection.checksum = checksum_before
            self.rollbacks += 1
            raise


async def test_migration_is_locked_atomic_and_second_execution_is_read_only(monkeypatch):
    module = migration_module()
    connection = Connection()
    engine = Engine(connection)
    validate = AsyncMock()
    monkeypatch.setattr(module, "validate_schema", validate)
    first = await module.migrate_stock_replay(engine)
    assert first["applied"] is True
    assert engine.commits == 1
    assert "pg_advisory_xact_lock" in connection.statements[2]
    assert connection.checksum == hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest()
    created_before = sum("CREATE TABLE stock_replay_" in sql for sql in connection.statements)
    assert created_before == 4
    second = await module.migrate_stock_replay(engine)
    assert second["applied"] is False
    assert sum("CREATE TABLE stock_replay_" in sql for sql in connection.statements) == created_before
    assert validate.await_count == 2


async def test_check_missing_version_does_not_create_any_structure(monkeypatch):
    module = migration_module()
    connection = Connection(exists=False)
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match="not applied|missing"):
        await module.migrate_stock_replay(engine, check=True)
    assert not any("CREATE " in sql or "INSERT " in sql for sql in connection.statements)
    assert engine.rollbacks == 1


async def test_checksum_conflict_aborts_without_rewriting_history(monkeypatch):
    module = migration_module()
    connection = Connection(checksum="unexpected")
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match="checksum"):
        await module.migrate_stock_replay(engine)
    assert not any("INSERT " in sql for sql in connection.statements)
    assert engine.rollbacks == 1


async def test_schema_failure_rolls_back_new_ddl_and_version(monkeypatch):
    module = migration_module()
    connection = Connection()
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await module.migrate_stock_replay(engine)
    assert engine.rollbacks == 1
    assert engine.commits == 0
    assert connection.checksum is None


def test_migration_sql_is_additive_and_has_expected_financial_constraints():
    module = migration_module()
    sql = module.MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DROP " not in sql.upper()
    assert "ALTER TABLE users" not in sql
    assert "\"current_date\" DATE NOT NULL" in sql
    assert "realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0" in sql
    assert "fees_paid NUMERIC(18, 2) NOT NULL DEFAULT 0" in sql
    assert "kind IN ('advance', 'cancel')" in sql
    assert "UNIQUE (account_id, from_revision)" in sql
    assert "REFERENCES users(id) ON DELETE RESTRICT" in sql
