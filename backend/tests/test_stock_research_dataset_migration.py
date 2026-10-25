import importlib.util
from pathlib import Path
import re
import sys

from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from app.models.base import Base
from app.models.stock_research_dataset import DatasetBase
from app.services.stock_research_v2.archive import SCHEMA_PATH


def test_additive_sql_matches_independent_tables_and_never_updates_original_market_data():
    sql = SCHEMA_PATH.read_text(encoding="utf8")
    assert not re.search(r"(?:^|;)\s*(ALTER|DROP|UPDATE|DELETE|TRUNCATE|INSERT)\b", sql, re.IGNORECASE)
    tables = {table.name for table in DatasetBase.metadata.sorted_tables} - {"stock_research_dataset_schema_versions"}
    assert set(re.findall(r"CREATE TABLE (\w+)", sql)) == tables
    for table in DatasetBase.metadata.sorted_tables:
        assert table.name not in Base.metadata.tables
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=postgresql.dialect()))
        if table.name in tables:
            for column in table.columns:
                assert re.search(rf"\b{column.name}\b", sql)
            for constraint in table.constraints:
                if constraint.name:
                    assert constraint.name in sql


def test_pg_reflected_check_equivalents_do_not_accept_modified_resource_limits():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("stock_dataset_migration_test", scripts / "migrate_stock_research_datasets.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))
    assert module.normalize_check("byte_size BETWEEN 1 AND 67108864") == module.normalize_check("((byte_size >= 1) AND (byte_size <= 67108864))")
    assert module.normalize_check("byte_size BETWEEN 1 AND 67108864") != module.normalize_check("byte_size BETWEEN 0 AND 67108864")
    assert module.normalize_check("state IN ('ready','quarantined')") == module.normalize_check("((state)::text = ANY ((ARRAY['ready'::character varying,'quarantined'::character varying])::text[]))")
