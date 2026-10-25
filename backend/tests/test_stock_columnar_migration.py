import ast
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.base import Base
from app.models.stock_columnar import ColumnarBase
from app.services.stock_research_v2.columnar import SCHEMA_PATH

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
RUNNER = SCRIPTS / "migrate_stock_columnar.py"


def subject():
    assert RUNNER.is_file(), "018 needs an explicit, check-by-default migration runner"
    spec = importlib.util.spec_from_file_location("columnar_migration_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_columnar_sql_adds_only_independent_tables_and_compiles_its_constraints():
    sql = re.sub(r"--[^\n]*", "", SCHEMA_PATH.read_text(encoding="utf-8"))
    assert not re.search(r"(?:^|;)\s*(ALTER|DROP|UPDATE|DELETE|TRUNCATE|INSERT)\b", sql, re.I)
    tables = {table.name for table in ColumnarBase.metadata.sorted_tables}
    assert set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)) == tables
    for table in ColumnarBase.metadata.sorted_tables:
        assert table.name not in Base.metadata.tables
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in ddl
        for column in table.columns:
            assert re.search(rf"\b{column.name}\b", sql)
        for constraint in table.constraints:
            if constraint.name:
                assert constraint.name in sql


def test_columnar_runner_defaults_to_readonly_and_requires_explicit_apply():
    module = subject()
    function = next(node for node in ast.parse(RUNNER.read_text(encoding="utf-8")).body if isinstance(node, ast.AsyncFunctionDef) and node.name == "migrate_stock_columnar")
    defaults = dict(zip((arg.arg for arg in function.args.kwonlyargs), function.args.kw_defaults))
    assert isinstance(defaults["check"], ast.Constant) and defaults["check"].value is True
    assert module.normalize_check("state IN ('ready','quarantined')") == module.normalize_check("((state)::text = ANY ((ARRAY['ready'::character varying,'quarantined'::character varying])::text[]))")
    assert module.normalize_check("state IN ('ready','quarantined')") != module.normalize_check("state IN ('ready','quarantined','unsafe')")
    assert module.normalize_check("length(input_hash) = 64") != module.normalize_check("length(input_hash) = 0")


def test_columnar_help_never_needs_a_database_or_columnar_runtime():
    assert RUNNER.is_file(), "018 runner must exist"
    result = subprocess.run([sys.executable, str(RUNNER), "--help"], cwd=SCRIPTS.parent,
        capture_output=True, text=True, timeout=10, env={key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"})
    assert result.returncode == 0, result.stderr
    assert "--check" in result.stdout and "--apply" in result.stdout


def test_columnar_ddl_does_not_execute_trailing_comments_or_unrelated_statements():
    module = subject()
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = module.migration_statements(sql)
    assert len(statements) == 3
    assert all(statement.startswith("CREATE ") and "--" not in statement for statement in statements)
    for invalid in [sql + "\nDROP TABLE users;", sql + "\nCREATE TABLE other_module(id int);", "-- comments only"]:
        with pytest.raises(RuntimeError):
            module.migration_statements(invalid)
