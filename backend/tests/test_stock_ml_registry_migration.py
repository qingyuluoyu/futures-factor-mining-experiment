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
from app.models.stock_ml_registry import MLRegistryBase
from app.services.stock_research_v2.model_registry import SCHEMA_PATH

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
RUNNER=SCRIPTS/'migrate_stock_ml_registry.py'


def subject():
    spec=importlib.util.spec_from_file_location('ml_registry_migration_test',RUNNER)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_019_is_additive_exact_orm_schema_with_explicit_readonly_runner():
    sql=re.sub(r'--[^\n]*','',SCHEMA_PATH.read_text(encoding='utf8'))
    assert not re.search(r'(?:^|;)\s*(ALTER|DROP|UPDATE|DELETE|TRUNCATE|INSERT)\b',sql,re.I)
    assert set(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)',sql))==set(MLRegistryBase.metadata.tables)
    for table in MLRegistryBase.metadata.sorted_tables:
        assert table.name not in Base.metadata.tables
        assert 'CREATE TABLE' in str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for column in table.columns: assert re.search(rf'\b{column.name}\b',sql)
        for constraint in table.constraints:
            if constraint.name: assert constraint.name in sql
    function=next(n for n in ast.parse(RUNNER.read_text(encoding='utf8')).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='migrate_stock_ml_registry')
    defaults=dict(zip((arg.arg for arg in function.args.kwonlyargs),function.args.kw_defaults))
    assert defaults['check'].value is True
    source=RUNNER.read_text(encoding='utf8')
    for text in ['SET TRANSACTION READ ONLY','lock_timeout','statement_timeout','pg_advisory_xact_lock(20261025019)','validate_parent_schema','PARENT_SCHEMA_PATH.read_bytes','validate_schema(connection)','postgres_port != 5433']:
        assert text in source
    assert 'public.' not in source


def test_runner_help_without_native_dependencies_or_database():
    result=subprocess.run([sys.executable,str(RUNNER),'--help'],cwd=SCRIPTS.parent,capture_output=True,text=True,timeout=10,env={key:value for key,value in os.environ.items() if key.upper()!='PYTHONPATH'})
    assert result.returncode==0,result.stderr
    assert '--apply' in result.stdout and '--check' in result.stdout


def test_ddl_allowlist_rejects_unrelated_mutation_and_check_normalization_is_not_lossy():
    module=subject(); sql=SCHEMA_PATH.read_text(encoding='utf8')
    assert len(module.migration_statements(sql))==3
    for invalid in [sql+'\nDROP TABLE users;',sql+'\nCREATE TABLE users(id int);','-- comment only']:
        with pytest.raises(RuntimeError): module.migration_statements(invalid)
    assert module.normalize_check("state IN ('ready','quarantined')")==module.normalize_check("((state)::text = ANY ((ARRAY['ready'::character varying,'quarantined'::character varying])::text[]))")
    assert module.normalize_check('fold_count BETWEEN 1 AND 5')!=module.normalize_check('fold_count BETWEEN 1 AND 500')
