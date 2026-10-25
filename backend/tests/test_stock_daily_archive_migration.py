import hashlib
from contextlib import asynccontextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, Integer, Numeric, UniqueConstraint


def migration_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_stock_daily_archive.py"
    assert path.exists(), "daily archive migration entry point is missing"
    spec = spec_from_file_location("stock_daily_archive_migration_test", path)
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
            return Result("stock_archive_schema_versions" if self.exists else None)
        if "SELECT checksum" in sql:
            return Result(self.checksum)
        if "INSERT INTO stock_archive_schema_versions" in sql:
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


async def test_first_repeat_and_check_are_atomic_locked_bounded_and_additive(monkeypatch):
    module = migration_module()
    connection, validate = Connection(), AsyncMock()
    engine = Engine(connection)
    monkeypatch.setattr(module, "validate_schema", validate)
    first = await module.migrate_stock_daily_archive(engine)
    assert first == {"version": "20261019_004_stock_daily_archive", "checksum": hashlib.sha256(module.MIGRATION_PATH.read_bytes()).hexdigest(), "applied": True, "checked": True}
    assert connection.statements[:3] == [
        ("SET LOCAL lock_timeout = '5s'", None),
        ("SET LOCAL statement_timeout = '30s'", None),
        ("SELECT pg_advisory_xact_lock(:lock_id)", {"lock_id": 20261019004}),
    ]
    assert (await module.migrate_stock_daily_archive(engine))["applied"] is False
    checkpoint = len(connection.statements)
    assert (await module.migrate_stock_daily_archive(engine, check=True))["applied"] is False
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in connection.statements[checkpoint:])
    assert sum("CREATE TABLE stock_daily_archive_batches" in sql for sql, _ in connection.statements) == 1
    assert validate.await_count == 3 and engine.commits == 3


@pytest.mark.parametrize("exists,checksum,error", [(False, None, "not applied"), (True, None, "not applied"), (True, "changed", "checksum")])
async def test_check_missing_or_changed_version_is_explicit_and_never_writes(monkeypatch, exists, checksum, error):
    module = migration_module()
    engine = Engine(Connection(checksum, exists))
    monkeypatch.setattr(module, "validate_schema", AsyncMock())
    with pytest.raises(RuntimeError, match=error):
        await module.migrate_stock_daily_archive(engine, check=True)
    assert not any("CREATE " in sql or "INSERT " in sql for sql, _ in engine.connection.statements)
    assert engine.rollbacks == 1 and engine.commits == 0


@pytest.mark.parametrize("failure", ["schema", "index"])
async def test_first_migration_failure_rolls_back_ddl_and_version(monkeypatch, failure):
    module = migration_module()
    engine = Engine(Connection(fail_at="CREATE INDEX" if failure == "index" else None))
    monkeypatch.setattr(module, "validate_schema", AsyncMock(side_effect=RuntimeError("schema mismatch") if failure == "schema" else None))
    with pytest.raises(RuntimeError):
        await module.migrate_stock_daily_archive(engine)
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
        columns = [{"name": column.name, "nullable": column.nullable, "type": column.type, "default": None} for column in self.metadata.tables[table].columns]
        if self.change == (table, "columns"):
            columns.pop()
        if self.change == (table, "nullability"):
            columns[0]["nullable"] = not columns[0]["nullable"]
        if self.change == (table, "type"):
            columns[0]["type"] = Integer()
        if self.change == (table, "precision"):
            next(column for column in columns if column["name"] == "volume")["type"] = Numeric(38, 17)
        if self.change == (table, "default"):
            columns[0]["default"] = "gen_random_uuid()"
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
            values[0]["dialect_options"] = {"postgresql_where": "close > 0"}
        if self.change == (table, "extra_index"):
            values.append({"name": "unexpected", "column_names": ["id"], "unique": False})
        return values

    def get_foreign_keys(self, table):
        values = [{"constrained_columns": [element.parent.name for element in constraint.elements], "referred_table": constraint.referred_table.name,
            "referred_columns": [element.column.name for element in constraint.elements], "referred_schema": None,
            "options": {"ondelete": constraint.ondelete}} for constraint in self.metadata.tables[table].foreign_key_constraints]
        if self.change == (table, "fk"):
            return []
        if self.change == (table, "fk_columns"):
            values[0]["constrained_columns"] = ["batch_id"]
        if self.change == (table, "fk_target"):
            values[0]["referred_columns"] = ["id", "archived_at"]
        if self.change == (table, "fk_delete"):
            values[0]["options"] = {"ondelete": "CASCADE"}
        if self.change == (table, "fk_deferrable"):
            values[0]["options"]["deferrable"] = True
        if self.change == (table, "fk_schema"):
            values[0]["referred_schema"] = "different_tenant"
        if self.change == (table, "extra_fk"):
            values.append({"constrained_columns": ["id"], "referred_table": "users", "referred_columns": ["id"], "options": {}})
        return values


class ReflectedConnection:
    async def run_sync(self, callback):
        return callback(self)


async def test_reflection_accepts_the_three_independent_archive_tables(monkeypatch):
    module = migration_module()
    assert set(module.ArchiveBase.metadata.tables) == {"stock_daily_archive_batches", "stock_daily_observations", "stock_archive_schema_versions"}
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.ArchiveBase.metadata))
    await module.validate_schema(ReflectedConnection())


@pytest.mark.parametrize("table,change", [
    ("stock_daily_archive_batches", name) for name in ("table", "columns", "nullability", "type", "default", "pk", "unique", "extra_unique", "check", "check_expression", "index", "index_order", "unique_index", "partial_index", "extra_index", "extra_fk")
] + [("stock_daily_observations", name) for name in ("precision", "fk", "fk_columns", "fk_target", "fk_delete", "fk_deferrable", "fk_schema")]
  + [("stock_archive_schema_versions", name) for name in ("table", "columns", "nullability", "type", "pk", "extra_fk", "extra_index")])
async def test_reflection_rejects_schema_drift(monkeypatch, table, change):
    module = migration_module()
    monkeypatch.setattr(module, "inspect", lambda _: Inspector(module.ArchiveBase.metadata, (table, change)))
    with pytest.raises(RuntimeError, match="missing|mismatch"):
        await module.validate_schema(ReflectedConnection())


def test_model_contract_has_decimal_bounds_composite_fk_and_no_shared_base():
    module = migration_module()
    from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
    from app.models.base import Base

    assert module.ArchiveBase.metadata is not Base.metadata
    batch, observation = StockDailyArchiveBatch.__table__, StockDailyObservation.__table__
    assert all(not column.nullable for table in module.ArchiveBase.metadata.tables.values() for column in table.columns)
    assert [column.name for column in observation.primary_key.columns] == ["batch_id", "instrument_id"]
    assert ("source_batch_id", "method_version") in {tuple(column.name for column in value.columns) for value in batch.constraints if isinstance(value, UniqueConstraint)}
    assert ("batch_id", "code") in {tuple(column.name for column in value.columns) for value in observation.constraints if isinstance(value, UniqueConstraint)}
    foreign = next(iter(observation.foreign_key_constraints))
    assert [(element.parent.name, element.column.name) for element in foreign.elements] == [("batch_id", "id"), ("trade_date", "trade_date")]
    assert foreign.ondelete == "RESTRICT"
    for name in ("open", "high", "low", "close", "pre_close", "volume", "amount"):
        assert (observation.c[name].type.precision, observation.c[name].type.scale) == (38, 18)
        expression = next(str(value.sqltext) for value in observation.constraints if isinstance(value, CheckConstraint) and value.name == f"ck_stock_daily_{name}")
        assert f"{name} < 100000000000000000000" in expression
        assert f"{name} {'>=' if name in ('volume', 'amount') else '>'} 0" in expression


def test_check_signature_preserves_numeric_arithmetic_boolean_grouping_and_literals():
    module = migration_module()
    expected = "row_count + missing_count = expected_count AND volume >= 0 AND volume < 100000000000000000000"
    reflected = "((((row_count + missing_count) = expected_count) AND (volume >= (0)::numeric)) AND (volume < '100000000000000000000'::numeric))"
    assert module._check_signature(expected) == module._check_signature(reflected)
    assert module._check_signature("(high >= open AND high >= close) OR low > 0") != module._check_signature("high >= open AND (high >= close OR low > 0)")
    assert module._check_signature("exchange = 'SSE'") != module._check_signature("exchange = 'sse'")
    assert module._check_signature("volume < 100000000000000000000") != module._check_signature("volume <= 100000000000000000000")
    with pytest.raises(RuntimeError, match="unsupported"):
        module._check_signature("volume > 0; DROP TABLE users")


def test_sql_is_additive_and_cli_redacts_underlying_error(monkeypatch, capsys):
    module = migration_module()
    sql = module.MIGRATION_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE stock_daily_archive_batches" in sql and "CREATE TABLE stock_daily_observations" in sql
    assert "NUMERIC(38, 18)" in sql and "ON DELETE RESTRICT" in sql
    assert "DROP " not in sql.upper() and "ALTER TABLE" not in sql.upper()
    monkeypatch.setattr(module, "_main", AsyncMock(side_effect=RuntimeError("postgresql://user:secret@private-host")))
    monkeypatch.setattr(module.sys, "argv", ["migrate_stock_daily_archive.py", "--check"])
    assert module.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and "secret" not in captured.err and "private-host" not in captured.err
    assert captured.out == ""


async def test_cli_disposes_engine_on_failure_without_real_connections(monkeypatch):
    module = migration_module()
    engine = type("DisposableEngine", (), {"dispose": AsyncMock()})()
    monkeypatch.setattr(module, "load_runtime_settings", lambda: object())
    monkeypatch.setattr(module, "create_replay_engine", lambda _: engine)
    monkeypatch.setattr(module, "migrate_stock_daily_archive", AsyncMock(side_effect=RuntimeError("schema mismatch")))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        await module._main(True)
    engine.dispose.assert_awaited_once()
