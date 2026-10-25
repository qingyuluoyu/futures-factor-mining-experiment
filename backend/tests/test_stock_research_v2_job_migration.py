import importlib.util
from pathlib import Path
import re
import sys

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable


def test_v2_job_migration_is_additive_independent_and_preserves_database_result_guards():
    from app.models.base import Base
    from app.models.stock_research_v2_job import V2JobBase
    from app.services.stock_research_v2.jobs import SCHEMA_PATH
    sql = SCHEMA_PATH.read_text(encoding="utf8")
    assert not re.search(r"(?:^|;)\s*(ALTER|DROP|UPDATE|DELETE|TRUNCATE|INSERT)\b", sql, re.IGNORECASE)
    tables = set(V2JobBase.metadata.tables) - {"stock_research_v2_job_schema_versions"}
    assert set(re.findall(r"CREATE TABLE (\w+)", sql)) == tables
    # Migration 021 additively widens these same two tables, so a column or
    # constraint the model declares may legitimately be written by either file.
    # 016 on its own must still stay free of destructive DDL and still create
    # exactly these tables; nothing above is relaxed by widening this lookup.
    widened = sql + (SCHEMA_PATH.parent / "20261025_021_stock_research_v2_task_chain.sql").read_text(encoding="utf8")
    for table in V2JobBase.metadata.sorted_tables:
        assert table.name not in Base.metadata.tables
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=postgresql.dialect()))
        if table.name not in tables:
            continue
        for column in table.columns:
            assert re.search(rf"\b{column.name}\b", widened)
        for constraint in table.constraints:
            if constraint.name:
                assert constraint.name in widened
    assert "result_data_version IS NOT NULL" in sql
    assert "result_qualification IS NOT NULL" in sql


def test_migration_runner_uses_explicit_readonly_check_checksum_and_full_schema_validation():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    path = scripts / "migrate_stock_research_v2_jobs.py"
    assert path.exists(), "missing independent 016 migration runner"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("stock_v2_job_migration_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))
    assert module.normalize_check("attempts BETWEEN 0 AND 1") == module.normalize_check("((attempts >= 0) AND (attempts <= 1))")
    assert module.normalize_check("attempts BETWEEN 0 AND 1") != module.normalize_check("attempts BETWEEN 0 AND 2")
    source = path.read_text(encoding="utf8")
    for fragment in ("SET TRANSACTION READ ONLY", "pg_advisory_xact_lock(20261023016)", "lock_timeout='5s'", "statement_timeout='30s'", "checksum", "convalidated", "get_foreign_keys", "get_indexes", "get_unique_constraints"):
        assert fragment in source
