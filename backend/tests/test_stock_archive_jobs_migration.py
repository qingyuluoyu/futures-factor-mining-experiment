import hashlib
from contextlib import asynccontextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, Integer, UniqueConstraint


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_stock_archive_jobs.py"
    assert path.exists(), "archive job migration entry point is missing"
    spec = spec_from_file_location("stock_archive_jobs_migration_test", path)
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
            return Result("stock_archive_job_schema_versions" if self.exists else None)
        if "SELECT checksum" in sql:
            return Result(self.checksum)
        if "INSERT INTO stock_archive_job_schema_versions" in sql:
            self.checksum = parameters["checksum"]
        return Result()


class Engine:
    def __init__(self, connection):
        self.connection, self.commits, self.rollbacks = connection, 0, 0

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


async def test_first_repeat_and_check_are_atomic_locked_bounded_and_additive(monkeypatch):
    module = migration_module()
    connection, validate = Connection(), AsyncMock()
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", validate)
    assert await module.migrate_stock_archive_jobs(engine) == {
        "version": "20261019_005_stock_archive_jobs", "checksum": hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest(),
        "applied": True, "checked": True,
    }
    assert connection.statements[:3] == [
        ("SET LOCAL lock_timeout = '5s'", None),
        ("SET LOCAL statement_timeout = '30s'", None),
        ("SELECT pg_advisory_xact_lock(:lock_id)", {"lock_id": 20261019005}),
    ]
    assert (await module.migrate_stock_archive_jobs(engine))["applied"] is False
    checkpoint = len(connection.statements)
    assert (await module.migrate_stock_archive_jobs(engine, check=True))["applied"] is False
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in connection.statements[checkpoint:])
    assert sum("CREATE TABLE stock_archive_jobs" in sql for sql, _ in connection.statements) == 1
    assert validate.await_count == 3 and engine.commits == 3


@pytest.mark.parametrize("exists,checksum,error", [(False, None, "not applied"), (True, None, "not applied"), (True, "changed", "checksum")])
async def test_check_missing_or_changed_version_never_writes(monkeypatch, exists, checksum, error):
    module = migration_module()
    engine = Engine(Connection(checksum, exists))
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match=error):
        await module.migrate_stock_archive_jobs(engine, check=True)
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in engine.connection.statements)
    assert engine.rollbacks == 1 and engine.commits == 0


@pytest.mark.parametrize("failure", ["schema", "index"])
async def test_migration_failure_rolls_back_ddl_and_version(monkeypatch, failure):
    module = migration_module()
    engine = Engine(Connection(fail_at="CREATE INDEX" if failure == "index" else None))
    monkeypatch.setattr(module, "validate_schema", AsyncMock(side_effect=RuntimeError("schema mismatch") if failure == "schema" else None))
    with pytest.raises(RuntimeError):
        await module.migrate_stock_archive_jobs(engine)
    assert engine.rollbacks == 1 and engine.commits == 0
    assert engine.connection.checksum is None and engine.connection.ddl == []
    assert not any("INSERT INTO" in sql for sql, _ in engine.connection.statements)


class Inspector:
    default_schema_name = "public"

    def __init__(self, metadata, change=None):
        self.metadata, self.change = metadata, change

    def has_table(self, table):
        return self.change != (table, "table")

    def get_columns(self, table):
        columns = [{"name": column.name, "nullable": column.nullable, "type": column.type,
            "default": str(column.server_default.arg) if column.server_default is not None else None}
            for column in self.metadata.tables[table].columns]
        if self.change == (table, "columns"):
            columns.pop()
        if self.change == (table, "nullability"):
            columns[0]["nullable"] = not columns[0]["nullable"]
        if self.change == (table, "type"):
            columns[0]["type"] = Integer()
        if self.change == (table, "default"):
            columns[0]["default"] = "gen_random_uuid()"
        for name, default in (("attempt_default_missing", None), ("attempt_default_changed", "2"), ("attempt_default_cast", "'1'::integer")):
            if self.change == (table, name):
                next(column for column in columns if column["name"] == "attempts")["default"] = default
        return columns

    def get_pk_constraint(self, table):
        return {"constrained_columns": [] if self.change == (table, "pk") else [column.name for column in self.metadata.tables[table].primary_key.columns]}

    def get_unique_constraints(self, table):
        values = [{"column_names": [column.name for column in constraint.columns]} for constraint in self.metadata.tables[table].constraints if isinstance(constraint, UniqueConstraint)]
        if self.change == (table, "unique"):
            return []
        if self.change == (table, "deferrable_unique"):
            values[0]["dialect_options"] = {"deferrable": True}
        return values + ([{"column_names": ["status"]}] if self.change == (table, "extra_unique") else [])

    def get_check_constraints(self, table):
        values = [{"name": constraint.name, "sqltext": str(constraint.sqltext)} for constraint in self.metadata.tables[table].constraints if isinstance(constraint, CheckConstraint)]
        if self.change == (table, "check"):
            return []
        if self.change == (table, "check_expression"):
            values[0]["sqltext"] = "1 = 1"
        if self.change == (table, "not_valid"):
            values[0]["dialect_options"] = {"not_valid": True}
        return values

    def get_indexes(self, table):
        values = [{"name": index.name, "column_names": [column.name for column in index.columns], "unique": index.unique} for index in self.metadata.tables[table].indexes]
        if self.change == (table, "index"):
            return []
        if self.change == (table, "index_order"):
            for value in values:
                value["column_names"].reverse()
        if self.change == (table, "unique_index"):
            values[0]["unique"] = True
        if self.change == (table, "partial_index"):
            values[0]["dialect_options"] = {"postgresql_where": "status = 'running'"}
        if self.change == (table, "extra_index"):
            values.append({"name": "unexpected", "column_names": ["id"], "unique": False})
        return values

    def get_foreign_keys(self, table):
        return [{"constrained_columns": ["id"], "referred_table": "users", "referred_columns": ["id"], "options": {}}] if self.change == (table, "extra_fk") else []


class ReflectedConnection:
    def __init__(self, change=None):
        self.change = change

    async def run_sync(self, callback):
        return callback(self)

    def execute(self, statement, parameters):
        catalog = {"relkind": b"r", "relpersistence": b"p", "unsafe_constraints": False, "unsafe_indexes": False}
        if "relation.relkind::text AS relkind" in str(statement):
            catalog["relkind"] = "r"
        if "relation.relpersistence::text AS relpersistence" in str(statement):
            catalog["relpersistence"] = "p"
        if self.change:
            catalog[self.change[0]] = self.change[1]
        return type("CatalogResult", (), {"mappings": lambda result: result, "one": lambda result: catalog})()


async def test_reflection_accepts_independent_tables_and_default_one(monkeypatch):
    module = migration_module()
    assert set(module.ArchiveJobBase.metadata.tables) == {"stock_archive_jobs", "stock_archive_job_schema_versions"}
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.ArchiveJobBase.metadata))
    await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("table,change", [
    ("stock_archive_jobs", name) for name in ("table", "columns", "nullability", "type", "default", "pk", "unique", "extra_unique", "deferrable_unique", "check", "check_expression", "not_valid", "index", "index_order", "unique_index", "partial_index", "extra_index", "extra_fk", "attempt_default_missing", "attempt_default_changed", "attempt_default_cast")
] + [("stock_archive_job_schema_versions", name) for name in ("table", "columns", "nullability", "type", "default", "pk", "extra_fk", "extra_index")])
async def test_reflection_rejects_schema_drift(monkeypatch, table, change):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.ArchiveJobBase.metadata, (table, change)))
    with pytest.raises(RuntimeError, match="missing|mismatch"):
        await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("change", [("relkind", "v"), ("relpersistence", "u"), ("unsafe_constraints", True), ("unsafe_indexes", True)])
async def test_reflection_rejects_deferred_unvalidated_constraints_invalid_indexes_and_non_tables(monkeypatch, change):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.ArchiveJobBase.metadata))
    with pytest.raises(RuntimeError, match="mismatch"):
        await module.validate_schema(ReflectedConnection(change))


def test_model_contract_is_isolated_and_complete():
    module = migration_module()
    from app.models.base import Base
    from app.models.stock_daily_archive import ArchiveBase
    from app.models.stock_archive_job import StockArchiveJob, StockArchiveJobVersion

    assert module.ArchiveJobBase.metadata is not Base.metadata
    assert module.ArchiveJobBase.metadata is not ArchiveBase.metadata
    assert StockArchiveJobVersion.__tablename__ == "stock_archive_job_schema_versions"
    table = StockArchiveJob.__table__
    assert set(table.columns.keys()) == {"id", "source_batch_id", "source_data_version", "method_version", "trade_date", "request_id", "status", "step", "attempts", "started_at", "updated_at", "deadline_at", "finished_at", "archive_batch_id", "error_code", "error_message"}
    assert {column.name for column in table.columns if column.nullable} == {"finished_at", "archive_batch_id", "error_code", "error_message"}
    assert table.c.attempts.default.arg == 1 and str(table.c.attempts.server_default.arg) == "1"
    assert not table.foreign_key_constraints
    assert {tuple(column.name for column in value.columns) for value in table.constraints if isinstance(value, UniqueConstraint)} == {("source_batch_id", "method_version")}
    assert {tuple(column.name for column in value.columns) for value in table.indexes} == {("status", "deadline_at"), ("started_at",)}


def test_check_signature_handles_nulls_casts_and_preserves_boolean_grouping():
    module = migration_module()
    assert module._check_signature("finished_at IS NULL AND status = 'running'") == module._check_signature("((finished_at IS NULL) AND ((status)::text = 'running'::text))")
    assert module._check_signature("finished_at IS NOT NULL") != module._check_signature("finished_at IS NULL")
    assert module._check_signature("status = 'running' AND (archive_batch_id IS NULL OR error_code IS NULL)") != module._check_signature("(status = 'running' AND archive_batch_id IS NULL) OR error_code IS NULL")
    for expression in ("status = 'RUNNING'", "status = 'running'"):
        assert module._check_signature(expression)
    assert module._check_signature("status = 'RUNNING'") != module._check_signature("status = 'running'")
    assert module._check_signature("status = 'running::text'") != module._check_signature("status = 'running'")
    for expression in ("finished_at IS TRUE", "finished_at IS NOT", "finished_at IS NULL; DROP TABLE users"):
        with pytest.raises(RuntimeError, match="unsupported"):
            module._check_signature(expression)


def test_sql_additive_and_cli_redacts_error(monkeypatch, capsys):
    module = migration_module()
    sql = module.MIGRATION_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE stock_archive_jobs" in sql and "attempts INTEGER NOT NULL DEFAULT 1" in sql
    assert "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper() and "REFERENCES" not in sql.upper()
    monkeypatch.setattr(module, "_main", AsyncMock(side_effect=RuntimeError("postgresql://user:secret@private-host")))
    monkeypatch.setattr(module.sys, "argv", ["migrate_stock_archive_jobs.py", "--check"])
    assert module.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and "secret" not in captured.err and "private-host" not in captured.err
    assert captured.out == ""


async def test_cli_disposes_engine_on_failure(monkeypatch):
    module = migration_module()
    engine = type("DisposableEngine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(module, "load_runtime_settings", lambda: object())
    monkeypatch.setattr(module, "create_replay_engine", lambda _: engine)
    monkeypatch.setattr(module, "migrate_stock_archive_jobs", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await module._main(True)
    engine.dispose.assert_awaited_once()
