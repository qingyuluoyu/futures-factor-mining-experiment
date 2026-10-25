import importlib.util
from pathlib import Path
import re
import sys

from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from app.models.stock_custom_factor import CustomFactorBase
from app.models.base import Base
from app.services.stock_research_v2.custom_factors import SCHEMA_PATH


def test_additive_migration_covers_models_and_never_joins_shared_create_all():
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    assert not re.search(r"(?:^|;)\s*(ALTER|DROP|TRUNCATE|DELETE|UPDATE|INSERT)\b", sql, re.IGNORECASE)
    expected = {table.name for table in CustomFactorBase.metadata.sorted_tables} - {"stock_custom_factor_schema_versions"}
    assert set(re.findall(r"CREATE TABLE (\w+)", sql)) == expected
    for table in CustomFactorBase.metadata.sorted_tables:
        assert table.name not in Base.metadata.tables
        compiled = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in compiled
        if table.name in expected:
            for column in table.columns:
                assert re.search(rf"\b{column.name}\b", sql)
            for constraint in table.constraints:
                if constraint.name:
                    assert constraint.name in sql


def test_pg_reflection_equivalents_and_corruption_remain_distinct():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location("stock_custom_migration_test", scripts / "migrate_stock_custom_factors.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))
    assert module.normalize_check("trading_mode IN ('virtual','live')") == module.normalize_check("((trading_mode)::text = ANY ((ARRAY['virtual'::character varying, 'live'::character varying])::text[]))")
    assert module.normalize_check("lookback BETWEEN 0 AND 244") == module.normalize_check("((lookback >= 0) AND (lookback <= 244))")
    assert module.normalize_check("lookback BETWEEN 0 AND 244") != module.normalize_check("lookback BETWEEN 0 AND 245")
