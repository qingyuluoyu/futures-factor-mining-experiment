import hashlib
from contextlib import asynccontextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, Integer, UniqueConstraint


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_stock_eod.py"
    assert path.exists(), "EOD migration entry point is missing"
    spec = spec_from_file_location("stock_eod_migration_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class Connection:
    def __init__(self, checksum=None, exists=True, fail_at=None):
        self.checksum, self.exists, self.fail_at = checksum, exists, fail_at
        self.statements = []
        self.ddl = []

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append((sql, parameters))
        if self.fail_at and self.fail_at in sql:
            raise RuntimeError("DDL failed")
        if "CREATE " in sql:
            self.ddl.append(sql)
        if "to_regclass" in sql:
            return Result("stock_eod_schema_versions" if self.exists else None)
        if "SELECT checksum" in sql:
            return Result(self.checksum)
        if "INSERT INTO stock_eod_schema_versions" in sql:
            self.checksum = parameters["checksum"]
        return Result()


class Engine:
    def __init__(self, connection):
        self.connection = connection
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def begin(self):
        checksum_before, ddl_before = self.connection.checksum, list(self.connection.ddl)
        try:
            yield self.connection
            self.commits += 1
        except Exception:
            self.connection.checksum, self.connection.ddl = checksum_before, ddl_before
            self.rollbacks += 1
            raise


async def test_first_repeat_and_check_are_locked_bounded_and_do_not_replay_batch_ddl(monkeypatch):
    module = migration_module()
    connection, validate = Connection(), AsyncMock()
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", validate)
    first = await module.migrate_stock_eod(engine)
    assert first == {"version": "20261018_003_stock_eod", "checksum": hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest(), "applied": True, "checked": True}
    assert connection.statements[:3] == [
        ("SET LOCAL lock_timeout = '5s'", None),
        ("SET LOCAL statement_timeout = '30s'", None),
        ("SELECT pg_advisory_xact_lock(:lock_id)", {"lock_id": 20261018004}),
    ]
    assert any("checksum CHAR(64) NOT NULL" in sql for sql, _ in connection.statements)
    assert (await module.migrate_stock_eod(engine))["applied"] is False
    checkpoint = len(connection.statements)
    assert (await module.migrate_stock_eod(engine, check=True))["applied"] is False
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in connection.statements[checkpoint:])
    assert sum("CREATE TABLE stock_eod_batches" in sql for sql, _ in connection.statements) == 1
    assert validate.await_count == 3
    assert engine.commits == 3


@pytest.mark.parametrize("exists,checksum,error", [(False, None, "not applied"), (True, None, "not applied"), (True, "changed", "checksum")])
async def test_check_missing_or_changed_version_is_explicit_and_never_writes(monkeypatch, exists, checksum, error):
    module = migration_module()
    engine = Engine(Connection(checksum, exists))
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match=error):
        await module.migrate_stock_eod(engine, check=True)
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in engine.connection.statements)
    assert engine.rollbacks == 1 and engine.commits == 0


@pytest.mark.parametrize("failure", ["schema", "index"])
async def test_first_migration_failure_never_commits_or_records_version(monkeypatch, failure):
    module = migration_module()
    engine = Engine(Connection(fail_at="CREATE INDEX" if failure == "index" else None))
    monkeypatch.setattr(module, "validate_schema", AsyncMock(side_effect=RuntimeError("schema mismatch") if failure == "schema" else None))
    with pytest.raises(RuntimeError):
        await module.migrate_stock_eod(engine)
    assert engine.rollbacks == 1 and engine.commits == 0
    assert engine.connection.checksum is None and engine.connection.ddl == []
    assert not any("INSERT INTO" in sql for sql, _ in engine.connection.statements)


async def test_existing_checksum_conflict_preserves_saved_version(monkeypatch):
    module = migration_module()
    engine = Engine(Connection(checksum="original"))
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match="checksum"):
        await module.migrate_stock_eod(engine)
    assert engine.connection.checksum == "original"
    assert not any("CREATE TABLE stock_eod_batches" in sql or "INSERT " in sql for sql, _ in engine.connection.statements)


class Inspector:
    def __init__(self, metadata, change=None):
        self.metadata, self.change = metadata, change

    def has_table(self, table):
        return self.change != (table, "table")

    def get_columns(self, table):
        columns = [{"name": column.name, "nullable": column.nullable, "type": column.type} for column in self.metadata.tables[table].columns]
        if self.change == (table, "columns"):
            columns.pop()
        if self.change == (table, "nullability"):
            columns[0]["nullable"] = not columns[0]["nullable"]
        if self.change == (table, "type"):
            columns[0]["type"] = Integer()
        return columns

    def get_pk_constraint(self, table):
        return {"constrained_columns": [] if self.change == (table, "pk") else [column.name for column in self.metadata.tables[table].primary_key.columns]}

    def get_unique_constraints(self, table):
        values = [{"column_names": [column.name for column in constraint.columns]} for constraint in self.metadata.tables[table].constraints if isinstance(constraint, UniqueConstraint)]
        if self.change == (table, "unique"):
            return []
        return values + ([{"column_names": ["status"]}] if self.change == (table, "extra_unique") else [])

    def get_check_constraints(self, table):
        values = [{"name": constraint.name, "sqltext": str(constraint.sqltext)} for constraint in self.metadata.tables[table].constraints if isinstance(constraint, CheckConstraint)]
        if self.change == (table, "check"):
            return []
        if self.change == (table, "check_expression"):
            values[0]["sqltext"] = "1 = 1"
        return values

    def get_indexes(self, table):
        values = [{"name": index.name, "column_names": [column.name for column in index.columns], "unique": index.unique} for index in self.metadata.tables[table].indexes]
        if self.change == (table, "index"):
            return []
        if self.change == (table, "index_order"):
            values[0]["column_names"].reverse()
        if self.change == (table, "unique_index"):
            values[0]["unique"] = True
        if self.change == (table, "partial_index"):
            values[0]["dialect_options"] = {"postgresql_where": "status = 'completed'"}
        return values

    def get_foreign_keys(self, table):
        return [{"constrained_columns": ["id"], "referred_table": "users", "referred_columns": ["id"]}] if self.change == (table, "fk") else []


class ReflectedConnection:
    async def run_sync(self, callback):
        return callback(self)


async def test_reflection_accepts_both_independent_eod_tables(monkeypatch):
    module = migration_module()
    assert set(module.EodBase.metadata.tables) == {"stock_eod_batches", "stock_eod_schema_versions"}
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.EodBase.metadata))
    await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("table,change", [
    ("stock_eod_batches", name) for name in ("table", "columns", "nullability", "type", "pk", "unique", "extra_unique", "check", "check_expression", "index", "index_order", "unique_index", "partial_index", "fk")
] + [("stock_eod_schema_versions", name) for name in ("table", "columns", "nullability", "type", "pk", "fk")])
async def test_reflection_rejects_schema_drift(monkeypatch, table, change):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.EodBase.metadata, (table, change)))
    with pytest.raises(RuntimeError, match="missing|mismatch"):
        await module.validate_schema(ReflectedConnection())


def test_eod_checks_reuse_the_existing_signature_and_preserve_grouping():
    module = migration_module()
    from migrate_stock_research_jobs import _check_signature
    assert module._check_signature is _check_signature
    expected = "(status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL)"
    reflected = "((((status)::text = 'running'::text) AND (finished_at IS NULL)) OR (((status)::text <> 'running'::text) AND (finished_at IS NOT NULL)))"
    changed = "status = 'running' AND (finished_at IS NULL OR status <> 'running') AND finished_at IS NOT NULL"
    assert module._check_signature(expected) == module._check_signature(reflected)
    assert module._check_signature(expected) != module._check_signature(changed)


def test_sql_remains_additive_and_cli_redacts_underlying_error(monkeypatch, capsys):
    module = migration_module()
    sql = module.MIGRATION_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE stock_eod_batches" in sql
    assert "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper()
    monkeypatch.setattr(module, "_main", AsyncMock(side_effect=RuntimeError("postgresql://user:secret@private-host")))
    monkeypatch.setattr(module.sys, "argv", ["migrate_stock_eod.py", "--check"])
    assert module.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "secret" not in captured.err and "private-host" not in captured.err
    assert captured.out == ""


async def test_cli_main_disposes_engine_on_failure_without_real_connections(monkeypatch):
    module = migration_module()
    engine = type("DisposableEngine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(module, "load_runtime_settings", lambda: object())
    monkeypatch.setattr(module, "create_replay_engine", lambda _: engine)
    monkeypatch.setattr(module, "migrate_stock_eod", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await module._main(True)
    engine.dispose.assert_awaited_once()
