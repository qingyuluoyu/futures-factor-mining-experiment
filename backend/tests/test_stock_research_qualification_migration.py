"""Package ⑤: static checks for migration 020 and its fail-closed readiness gate.

Nothing here touches a real database: the runner is exercised against throwaway
in-memory SQLite engines, and the real deployment path (loopback PG5433) is only
referenced through the runner's own guard.
"""
from pathlib import Path
import re

import pytest
from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_ROOT / "migrations"
PROTECTED = ("20261023_015_stock_research_datasets", "20261023_016_stock_research_v2_jobs",
    "20261023_017_stock_custom_factors", "20261024_018_stock_columnar_datasets", "20261025_019_stock_ml_registry")


def runner():
    import sys

    sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
    import migrate_stock_research_qualification as module
    return module


def sql_check_text():
    """The grade CHECK exactly as the migration writes it, without running it."""
    source = (MIGRATIONS / "20261025_020_stock_research_qualification.sql").read_text(encoding="utf8")
    match = re.search(r"ADD\s+CONSTRAINT\s+ck_stock_dataset_qualification\s+CHECK\s*\(([^;]*?)\)\s*;", source, re.IGNORECASE | re.DOTALL)
    assert match is not None
    return match.group(1).strip()


def test_020_is_the_only_free_version_and_protected_migrations_are_untouched():
    from app.services.stock_research_v2.qualification import SCHEMA_VERSION, SCHEMA_PATH
    assert SCHEMA_VERSION == "20261025_020_stock_research_qualification"
    assert SCHEMA_PATH == MIGRATIONS / f"{SCHEMA_VERSION}.sql" and SCHEMA_PATH.is_file()
    taken = sorted(path.name for path in MIGRATIONS.glob("20261025_020_*.sql"))
    assert taken == [SCHEMA_PATH.name]
    for name in PROTECTED:
        assert (MIGRATIONS / f"{name}.sql").is_file(), name
    # 014 is a known pre-existing duplicate; it is neither of the files 020 owns.
    assert len(list(MIGRATIONS.glob("*014*.sql"))) >= 1


def test_migration_is_purely_additive_and_allowlisted():
    module = runner()
    statements = module.migration_statements()
    assert len(statements) == 3
    assert sum(statement.upper().startswith("ALTER TABLE") for statement in statements) == 2
    assert any("DROP CONSTRAINT IF EXISTS ck_stock_dataset_qualification" in statement for statement in statements)
    assert any("ADD CONSTRAINT ck_stock_dataset_qualification" in statement for statement in statements)
    assert all(statement.upper().startswith(("ALTER TABLE", "CREATE TABLE")) for statement in statements)
    with pytest.raises(module.MigrationError):
        module.migration_statements("ALTER TABLE users DROP COLUMN id;")


def test_sql_and_model_agree_on_the_grade_allowlist():
    from app.services.stock_research_v2.qualification import expected_grade, grade_allowance
    assert grade_allowance(sql_check_text()) == ("qualification", ("backtest_ready", "observation_only", "research_only"))
    assert expected_grade() == grade_allowance(sql_check_text())


def test_legacy_single_grade_check_is_rejected_as_a_mismatch():
    from app.services.stock_research_v2.qualification import expected_grade, grade_allowance
    legacy = "qualification = 'observation_only'"
    with pytest.raises(ValueError):
        grade_allowance(legacy)
    assert expected_grade() != ("qualification", ("observation_only",))


def register_020(connection, *, checksum=None, legacy=False):
    """Simulate a migrated schema without running the PostgreSQL-only DDL.

    Migration 020's ``ALTER TABLE ... DROP/ADD CONSTRAINT`` is PostgreSQL DDL that
    SQLite cannot execute, so the registered state is built directly: the dataset
    table either keeps the model-mirrored relaxed CHECK or is rebuilt with the old
    single-grade CHECK, and the version table gets the runner's own DDL.  Nothing
    here mutates the SQLAlchemy model.
    """
    from sqlalchemy import CheckConstraint, Column, DateTime, MetaData, String, Table
    from sqlalchemy.dialects.postgresql import UUID
    from sqlalchemy.schema import CreateTable
    if legacy:
        rebuilt = Table("stock_research_datasets", MetaData(),
            Column("id", UUID(as_uuid=True), primary_key=True),
            Column("manifest_artifact_id", UUID(as_uuid=True), nullable=False),
            Column("data_version", String(64), nullable=False),
            Column("qualification", String(24), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            CheckConstraint("qualification = 'observation_only'", name="ck_stock_dataset_qualification"))
        connection.execute(text("DROP TABLE stock_research_datasets"))
        connection.execute(CreateTable(rebuilt))
    connection.execute(text("CREATE TABLE IF NOT EXISTS stock_research_qualification_schema_versions "
        "(version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
    connection.execute(text("INSERT INTO stock_research_qualification_schema_versions(version,checksum) VALUES ('"
        + SCHEMA_VERSION + "','" + (checksum or CHECKSUM) + "')"))


SCHEMA_VERSION = "20261025_020_stock_research_qualification"
CHECKSUM = __import__("hashlib").sha256((MIGRATIONS / f"{SCHEMA_VERSION}.sql").read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_check_refuses_a_missing_version_row_and_passes_once_registered():
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    module = runner()
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            bind = session.get_bind()
            with pytest.raises(module.MigrationError) as refused:
                await module.migrate_stock_research_qualification(bind, check=True)
            assert "not applied" in str(refused.value)
            with bind.connect() as connection:
                register_020(connection)
                connection.commit()
            checked = await module.migrate_stock_research_qualification(bind, check=True)
            assert checked["applied"] is False and checked["checksum"] == CHECKSUM
            with bind.connect() as connection:
                module.validate_schema(connection)


@pytest.mark.asyncio
async def test_registered_checksum_mismatch_refuses_service():
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    module = runner()
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            bind = session.get_bind()
            with bind.connect() as connection:
                register_020(connection, checksum="0" * 64)
                connection.commit()
            with pytest.raises(module.MigrationError) as mismatch:
                await module.migrate_stock_research_qualification(bind, check=True)
            assert "checksum mismatch" in str(mismatch.value)


@pytest.mark.asyncio
async def test_legacy_single_grade_constraint_is_refused_by_schema_validation():
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    module = runner()
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            bind = session.get_bind()
            with bind.connect() as connection:
                register_020(connection, legacy=True)
                connection.commit()
            with pytest.raises(module.MigrationError) as legacy:
                await module.migrate_stock_research_qualification(bind, check=True)
            assert "grade check" in str(legacy.value)


@pytest.mark.asyncio
async def test_qualification_ready_refuses_before_and_accepts_a_registered_migration():
    from app.services.stock_research_v2.qualification import qualification_ready
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            assert await qualification_ready(session) is False
            bind = session.get_bind()
            with bind.connect() as connection:
                register_020(connection)
                connection.commit()
            assert await qualification_ready(session) is True
            with bind.connect() as connection:
                connection.execute(text("UPDATE stock_research_qualification_schema_versions SET checksum='0'*64"))
                connection.commit()
            assert await qualification_ready(session) is False


@pytest.mark.asyncio
async def test_missing_version_table_never_auto_creates_anything():
    from app.services.stock_research_v2 import qualification
    from tests.test_stock_research_job_service import job_database
    from sqlalchemy import text
    async with job_database() as (factory, owner, other):
        async with factory() as session:
            # No 015 schema at all: readiness is False and nothing was created.
            assert await qualification.qualification_ready(session) is False
            assert await session.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='stock_research_qualification_schema_versions'")) == 0
            assert qualification.SCHEMA_PATH.name.startswith("20261025_020_")
