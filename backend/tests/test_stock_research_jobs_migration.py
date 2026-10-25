import hashlib
from contextlib import asynccontextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_stock_research_jobs.py"
    assert path.exists(), "research migration entry point is missing"
    spec = spec_from_file_location("stock_research_migration_test", path)
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
        self.statements.append((sql, params))
        if "to_regclass" in sql:
            return Result("stock_research_schema_versions" if self.exists else None)
        if "SELECT checksum" in sql:
            return Result(self.checksum)
        if "INSERT INTO stock_research_schema_versions" in sql:
            self.checksum = params["checksum"]
        return Result()


class Engine:
    def __init__(self, connection):
        self.connection = connection
        self.commits = 0
        self.rollbacks = 0

    @asynccontextmanager
    async def begin(self):
        previous = self.connection.checksum
        try:
            yield self.connection
            self.commits += 1
        except Exception:
            self.connection.checksum = previous
            self.rollbacks += 1
            raise


async def test_first_repeat_and_check_validate_without_replaying_ddl(monkeypatch):
    module = migration_module()
    connection = Connection()
    engine = Engine(connection)
    validate = AsyncMock()
    monkeypatch.setattr(module, "validate_schema", validate)
    first = await module.migrate_stock_research_jobs(engine)
    assert first == {"version": "20261018_002_stock_research_jobs", "checksum": hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest(), "applied": True, "checked": True}
    assert "pg_advisory_xact_lock" in connection.statements[2][0]
    assert connection.statements[2][1] == {"lock_id": 20261018002}
    initial_creates = sum("CREATE TABLE stock_research_jobs" in sql for sql, _ in connection.statements)
    assert initial_creates == 1
    assert (await module.migrate_stock_research_jobs(engine))["applied"] is False
    start = len(connection.statements)
    assert (await module.migrate_stock_research_jobs(engine, check=True))["applied"] is False
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in connection.statements[start:])
    assert sum("CREATE TABLE stock_research_jobs" in sql for sql, _ in connection.statements) == initial_creates
    assert validate.await_count == 3
    assert engine.commits == 3


@pytest.mark.parametrize("exists,checksum,error", [(False, None, "not applied"), (True, None, "not applied"), (True, "changed", "checksum")])
async def test_check_rejects_missing_or_changed_version_without_ddl(monkeypatch, exists, checksum, error):
    module = migration_module()
    connection = Connection(checksum, exists)
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match=error):
        await module.migrate_stock_research_jobs(engine, check=True)
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in connection.statements)
    assert engine.rollbacks == 1


async def test_schema_failure_rolls_back_without_recording_version(monkeypatch):
    module = migration_module()
    engine = Engine(Connection())
    monkeypatch.setattr(module, "validate_schema", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await module.migrate_stock_research_jobs(engine)
    assert engine.connection.checksum is None and engine.commits == 0 and engine.rollbacks == 1


def test_sql_is_additive_and_contains_no_executable_function_body():
    module = migration_module()
    sql = module.MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DROP " not in sql.upper() and "ALTER TABLE users" not in sql
    assert "REFERENCES users(id) ON DELETE RESTRICT" in sql
    assert "request_hash CHAR(64) NOT NULL" in sql
    assert "UNIQUE (user_id, trading_mode, idempotency_key)" in sql
    assert "result_bytes <= 8388608" in sql


class Inspector:
    def __init__(self, metadata):
        self.metadata = metadata
        self.change = None

    def has_table(self, table):
        return self.change != (table, "table")

    def get_columns(self, table):
        columns = [{"name": column.name, "nullable": column.nullable, "type": column.type} for column in self.metadata.tables[table].columns]
        if self.change == (table, "nullability"):
            columns[0]["nullable"] = not columns[0]["nullable"]
        if self.change == (table, "type"):
            from sqlalchemy import Integer
            columns[0]["type"] = Integer()
        if self.change == (table, "columns"):
            columns.pop()
        return columns

    def get_pk_constraint(self, table):
        return {"constrained_columns": [] if self.change == (table, "pk") else [column.name for column in self.metadata.tables[table].primary_key.columns]}

    def get_unique_constraints(self, table):
        values = [{"column_names": [column.name for column in constraint.columns]} for constraint in self.metadata.tables[table].constraints if isinstance(constraint, UniqueConstraint)]
        return [] if self.change == (table, "unique") else values

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
        if self.change == (table, "partial_index"):
            values[0]["dialect_options"] = {"postgresql_where": "status = 'completed'"}
        if self.change == (table, "unique_index"):
            values[0]["unique"] = True
        return values

    def get_foreign_keys(self, table):
        values = [{"constrained_columns": [element.parent.name for element in constraint.elements], "referred_table": constraint.referred_table.name, "referred_columns": [element.column.name for element in constraint.elements], "options": {"ondelete": constraint.ondelete}} for constraint in self.metadata.tables[table].foreign_key_constraints]
        if self.change == (table, "fk"):
            values[0]["options"]["ondelete"] = "CASCADE"
        return values


class ReflectedConnection:
    async def run_sync(self, function):
        function(self)


async def test_validator_accepts_model_contract_and_version_table(monkeypatch):
    module = migration_module()
    inspector = Inspector(module.ResearchJobBase.metadata)
    monkeypatch.setattr(module, "inspect", lambda _: inspector)
    await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("table,change", [
    ("stock_research_jobs", name) for name in ("table", "columns", "nullability", "type", "pk", "unique", "check", "check_expression", "index", "partial_index", "unique_index", "fk")
] + [("stock_research_schema_versions", name) for name in ("table", "columns", "nullability", "type", "pk")])
async def test_validator_rejects_schema_drift(monkeypatch, table, change):
    module = migration_module()
    inspector = Inspector(module.ResearchJobBase.metadata)
    inspector.change = table, change
    monkeypatch.setattr(module, "inspect", lambda _: inspector)
    with pytest.raises(RuntimeError, match="missing|mismatch"):
        await module.validate_schema(ReflectedConnection())


def test_check_expression_validation_preserves_boolean_grouping():
    module = migration_module()
    expected = "status <> 'running' OR (worker_token IS NOT NULL AND started_at IS NOT NULL)"
    reflected = "(((status)::text <> 'running'::text) OR ((worker_token IS NOT NULL) AND (started_at IS NOT NULL)))"
    changed = "(status <> 'running' OR worker_token IS NOT NULL) AND started_at IS NOT NULL"
    assert module._check_signature(expected) == module._check_signature(reflected)
    assert module._check_signature(expected) != module._check_signature(changed)
