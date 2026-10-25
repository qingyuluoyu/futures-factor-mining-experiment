"""Package ④-1/④-2: additive migration 021, relaxed 016 contracts and the task-chain mount.

Nothing here touches a real database or a running service: the runner is exercised
against throwaway in-memory SQLite engines, the router mounts are asserted on the
real ``create_app()`` route table, and the real deployment path (loopback PG5433)
is only referenced through the runner's own guard.
"""
from pathlib import Path
import re
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_ROOT / "migrations"
SCRIPTS = BACKEND_ROOT / "scripts"
CHAIN_VERSION = "20261025_021_stock_research_v2_task_chain"
CHAIN_PATH = MIGRATIONS / f"{CHAIN_VERSION}.sql"
CHAIN_TABLE = "stock_research_v2_task_chain_schema_versions"
ADVISORY_LOCK = 20261025021
CHECKSUM = __import__("hashlib").sha256(CHAIN_PATH.read_bytes()).hexdigest()

# Byte anchors captured before this package touched anything (logs/pkg4-20261026/before-inventory.txt).
PROTECTED = {
    "20261023_015_stock_research_datasets.sql": "8b901fcaf6be12ad",
    "20261023_016_stock_research_v2_jobs.sql": "ef84a5c47e1f3a0a",
    "20261023_017_stock_custom_factors.sql": "1bcaeca477ee736c",
    "20261024_018_stock_columnar_datasets.sql": "047001d8f5e94036",
    "20261025_019_stock_ml_registry.sql": "e00b52ee45e9cfeb",
    "20261025_020_stock_research_qualification.sql": "b1d678a2c6b56af1",
}
RELAXED = ("ck_stock_v2_job_kind", "ck_stock_v2_job_result", "ck_stock_v2_job_runtime", "ck_stock_v2_worker_name")
RESULT_COLUMNS = ("result_experiment_id", "result_report_sha256", "result_artifact_id")
KINDS = ("dataset", "research_ic", "backtest", "benchmark", "ml_train", "report")
BASE = "/api/stock/research/v2"


def runner():
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import migrate_stock_research_v2_task_chain as module
    return module


def model_checks():
    from app.models.stock_research_v2_job import StockResearchV2Job, StockResearchV2Worker
    checks = {}
    for table in (StockResearchV2Job.__table__, StockResearchV2Worker.__table__):
        for item in table.constraints:
            if hasattr(item, "sqltext"):
                checks[item.name] = str(item.sqltext)
    return checks


def sql_constraint(name):
    """The CHECK text migration 021 writes for ``name``, without running it."""
    source = CHAIN_PATH.read_text(encoding="utf8")
    match = re.search(r"ADD\s+CONSTRAINT\s+" + name + r"\s+CHECK\s*\(([^;]*?)\)\s*;", source, re.IGNORECASE | re.DOTALL)
    assert match is not None, f"migration 021 does not relax {name}"
    return match.group(1).strip()


def postgres_spelling(expression):
    """Render a model CHECK the way ``pg_get_constraintdef`` spells it.

    PostgreSQL stores ``x IN (a,b)`` as the ScalarArrayOp ``x = ANY (ARRAY[...])``
    and ``BETWEEN`` as a pair of comparisons, so a name-only comparison would pass
    on SQLite and silently diverge on the real deployment database.
    """
    value = re.sub(r"'([A-Za-z_][A-Za-z_0-9]*)'", r"'\1'::text", expression)
    value = re.sub(r"\b([a-z_][a-z_0-9]*) IN \(([^)]*)\)",
        lambda m: m.group(1) + " = ANY (ARRAY[" + m.group(2) + "])", value, flags=re.IGNORECASE)
    value = re.sub(r"\b([a-z_][a-z_0-9]*) BETWEEN ([0-9]+) AND ([0-9]+)", r"(\1 >= \2) AND (\1 <= \3)", value, flags=re.IGNORECASE)
    return "(" + value + ")"


def register_chain(connection, *, checksum=None, legacy_kind=False):
    """Simulate a migrated schema without running the PostgreSQL-only DDL.

    Migration 021's ``ALTER TABLE ... DROP/ADD CONSTRAINT`` is PostgreSQL DDL that
    SQLite cannot execute, so the registered state is built directly: the job table
    either keeps the model-mirrored relaxed CHECK or is rebuilt with the old
    single-kind CHECK, and the version table gets the runner's own DDL.  Nothing
    here mutates the SQLAlchemy model.
    """
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, MetaData
    from sqlalchemy.schema import CreateTable
    from app.models.stock_research_v2_job import StockResearchV2Job
    if legacy_kind:
        legacy = StockResearchV2Job.__table__.to_metadata(MetaData())
        for item in [item for item in legacy.constraints if hasattr(item, "sqltext")
                     or item.__class__.__name__ == "ForeignKeyConstraint"]:
            legacy.constraints.discard(item)
        legacy.append_constraint(CheckConstraint("kind = 'dataset'", name="ck_stock_v2_job_kind"))
        connection.execute(text("DROP TABLE stock_research_v2_jobs"))
        connection.execute(CreateTable(legacy))
    connection.execute(text(f"CREATE TABLE IF NOT EXISTS {CHAIN_TABLE} "
        "(version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
    connection.execute(text(f"INSERT INTO {CHAIN_TABLE}(version,checksum) VALUES ('" + CHAIN_VERSION + "','" + (checksum or CHECKSUM) + "')"))


def prepare_registry(factory):
    """Create the 016/019 tables and register their version rows on a throwaway engine."""
    from app.models.stock_ml_registry import MLRegistryBase, MLRegistrySchemaVersion
    from app.models.stock_research_v2_job import V2JobBase, V2JobSchemaVersion
    from app.services.stock_research_v2.archive import SCHEMA_PATH as DATASET_PATH, SCHEMA_VERSION as DATASET_VERSION
    from app.services.stock_research_v2.jobs import SCHEMA_PATH as V2_PATH, SCHEMA_VERSION as V2_VERSION
    from app.services.stock_research_v2.model_registry import SCHEMA_PATH as ML_PATH, SCHEMA_VERSION as ML_VERSION
    import hashlib

    async def build():
        async with factory() as session:
            session.sync.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
            V2JobBase.metadata.create_all(session.get_bind())
            MLRegistryBase.metadata.create_all(session.get_bind())
            session.add(V2JobSchemaVersion(version=V2_VERSION, checksum=hashlib.sha256(V2_PATH.read_bytes()).hexdigest()))
            session.add(MLRegistrySchemaVersion(version=ML_VERSION, checksum=hashlib.sha256(ML_PATH.read_bytes()).hexdigest()))
            await session.commit()

    return build()


# --------------------------------------------------------------------------- ④-1 migration


def test_021_is_the_next_free_version_and_every_protected_migration_is_byte_identical():
    assert CHAIN_PATH.is_file(), "migration 021 is missing"
    taken = sorted(path.name for path in MIGRATIONS.glob("20261025_02*.sql"))
    assert taken == ["20261025_020_stock_research_qualification.sql", f"{CHAIN_VERSION}.sql"]
    for name, prefix in PROTECTED.items():
        digest = __import__("hashlib").sha256((MIGRATIONS / name).read_bytes()).hexdigest()
        assert digest.startswith(prefix), f"{name} changed: {digest}"
    # 014 is a known pre-existing duplicate; it is neither of the files 021 owns.
    assert len(list(MIGRATIONS.glob("*014*.sql"))) >= 1


def test_migration_is_purely_additive_and_allowlisted():
    module = runner()
    statements = module.migration_statements()
    assert statements
    forbidden = r"\b(DROP\s+TABLE|DROP\s+COLUMN|TRUNCATE|UPDATE\s+\w|INSERT\s+INTO|DELETE\s+FROM|GRANT|REVOKE|COPY\s+\w)"
    assert not any(re.search(forbidden, statement, re.IGNORECASE) for statement in statements)
    assert all(statement.upper().startswith(("ALTER TABLE", "CREATE TABLE")) for statement in statements)
    assert sum(statement.upper().startswith("ALTER TABLE") for statement in statements) == 11
    assert sum("DROP CONSTRAINT IF EXISTS" in statement for statement in statements) == 4
    assert sum("ADD CONSTRAINT" in statement for statement in statements) == 4
    assert sum("ADD COLUMN IF NOT EXISTS" in statement for statement in statements) == 3
    for name in RELAXED:
        assert any(f"DROP CONSTRAINT IF EXISTS {name}" in statement for statement in statements)
        assert any(f"ADD CONSTRAINT {name} CHECK" in statement for statement in statements)
    for column in RESULT_COLUMNS:
        assert any(f"ADD COLUMN IF NOT EXISTS {column}" in statement for statement in statements)
    with pytest.raises(module.MigrationError):
        module.migration_statements("ALTER TABLE stock_research_v2_jobs DROP COLUMN result_dataset_id;")
    with pytest.raises(module.MigrationError):
        module.migration_statements("UPDATE stock_research_v2_jobs SET kind='ml_train';")
    with pytest.raises(module.MigrationError):
        module.migration_statements("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_pkey;")
    with pytest.raises(module.MigrationError):
        module.migration_statements("INSERT INTO stock_research_v2_jobs(kind) VALUES ('ml_train');")


def test_sql_and_model_agree_on_every_relaxed_constraint_under_both_dialect_spellings():
    module = runner()
    from scripts.migrate_stock_research_datasets import normalize_check
    checks = model_checks()
    for name in RELAXED:
        assert name in checks, f"model lost {name}"
        assert normalize_check(sql_constraint(name)) == normalize_check(checks[name]), name
        assert normalize_check(postgres_spelling(checks[name])) == normalize_check(checks[name]), name
    assert "kind IN ('dataset','research_ic','backtest','benchmark','ml_train','report')" in sql_constraint("ck_stock_v2_job_kind")
    assert "name IN ('stock-v2-dataset-job-v1','stock-v2-research-task-v1')" in sql_constraint("ck_stock_v2_worker_name")
    assert "BETWEEN 30 AND 900" in sql_constraint("ck_stock_v2_job_runtime")
    for shape in ("result_dataset_id IS NOT NULL", "result_experiment_id IS NOT NULL", "result_artifact_id IS NOT NULL"):
        assert shape in sql_constraint("ck_stock_v2_job_result")


def test_legacy_single_kind_and_single_result_spellings_are_refused():
    module = runner()
    from scripts.migrate_stock_research_datasets import normalize_check
    checks = model_checks()
    for name, legacy in (("ck_stock_v2_job_kind", "kind = 'dataset'"),
                         ("ck_stock_v2_worker_name", "name = 'stock-v2-dataset-job-v1'"),
                         ("ck_stock_v2_job_runtime", "max_runtime_seconds BETWEEN 30 AND 300")):
        assert normalize_check(legacy) != normalize_check(checks[name]), name
    legacy_result = (sql_constraint("ck_stock_v2_job_result")
        .replace("result_experiment_id IS NULL AND ", "").replace("result_report_sha256 IS NULL AND ", "")
        .replace("result_artifact_id IS NULL", ""))
    assert normalize_check(legacy_result) != normalize_check(checks["ck_stock_v2_job_result"])


def test_new_result_columns_are_nullable_and_only_reference_015_and_019_tables():
    from app.models.stock_research_v2_job import StockResearchV2Job
    columns = StockResearchV2Job.__table__.columns
    for name in RESULT_COLUMNS:
        assert name in columns and columns[name].nullable, name
    references = {}
    for item in StockResearchV2Job.__table__.foreign_key_constraints:
        references[item.column_keys[0]] = next(iter(item.elements)).column.table.name
    assert references["result_dataset_id"] == "stock_research_datasets"
    assert references["result_experiment_id"] == "stock_ml_experiments"
    assert references["result_artifact_id"] == "stock_research_artifacts"
    assert columns["result_report_sha256"].type.length == 64
    # 019 tables are referenced, never rebuilt here.
    from app.models.stock_ml_registry import MLRegistryBase
    assert "stock_ml_experiments" in MLRegistryBase.metadata.tables
    assert "stock_ml_experiments" not in {table.name for table in StockResearchV2Job.__table__.metadata.sorted_tables}


def test_runner_uses_its_own_lock_version_checksum_and_readonly_check():
    module = runner()
    assert module.SCHEMA_VERSION == CHAIN_VERSION
    assert module.SCHEMA_PATH == CHAIN_PATH
    assert module.ADVISORY_LOCK == ADVISORY_LOCK
    assert module.VERSION_TABLE == CHAIN_TABLE
    assert module.checksum == CHECKSUM
    source = (SCRIPTS / "migrate_stock_research_v2_task_chain.py").read_text(encoding="utf8")
    for fragment in ("SET TRANSACTION READ ONLY", "pg_advisory_xact_lock", "lock_timeout='5s'",
                     "statement_timeout='30s'", "checksum", "convalidated", "get_foreign_keys", "get_indexes", "get_unique_constraints"):
        assert fragment in source
    # Exactly one advisory lock, and it is the 021 one: no other lock id can hide here.
    assert source.count("pg_advisory_xact_lock") == 1
    assert "pg_advisory_xact_lock({ADVISORY_LOCK})" in source
    assert not re.search(r"pg_advisory_xact_lock\(\d+\)", source)
    # The 016 runner keeps its own lock and version row; 021 never reuses either.
    legacy = (SCRIPTS / "migrate_stock_research_v2_jobs.py").read_text(encoding="utf8")
    assert "pg_advisory_xact_lock(20261023016)" in legacy and f"pg_advisory_xact_lock({ADVISORY_LOCK})" not in legacy


@pytest.mark.asyncio
async def test_check_refuses_a_missing_version_row_and_passes_once_registered():
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    module = runner()
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        await prepare_registry(factory)
        async with factory() as session:
            bind = session.get_bind()
            with pytest.raises(module.MigrationError) as refused:
                await module.migrate_stock_research_v2_task_chain(bind, check=True)
            assert "not applied" in str(refused.value)
            with bind.connect() as connection:
                register_chain(connection)
                connection.commit()
            checked = await module.migrate_stock_research_v2_task_chain(bind, check=True)
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
        await prepare_registry(factory)
        async with factory() as session:
            bind = session.get_bind()
            with bind.connect() as connection:
                register_chain(connection, checksum="0" * 64)
                connection.commit()
            with pytest.raises(module.MigrationError) as mismatch:
                await module.migrate_stock_research_v2_task_chain(bind, check=True)
            assert "checksum mismatch" in str(mismatch.value)


@pytest.mark.asyncio
async def test_legacy_single_kind_constraint_is_refused_by_schema_validation():
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    module = runner()
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        await prepare_registry(factory)
        async with factory() as session:
            bind = session.get_bind()
            with bind.connect() as connection:
                register_chain(connection, legacy_kind=True)
                connection.commit()
            with pytest.raises(module.MigrationError) as legacy:
                await module.migrate_stock_research_v2_task_chain(bind, check=True)
            assert "check mismatch" in str(legacy.value)


@pytest.mark.asyncio
async def test_missing_parent_migrations_never_auto_create_anything():
    from tests.test_stock_research_job_service import job_database
    module = runner()
    async with job_database() as (factory, owner, other):
        async with factory() as session:
            with pytest.raises(module.MigrationError):
                await module.migrate_stock_research_v2_task_chain(session.get_bind(), check=True)
            assert await session.scalar(text(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{CHAIN_TABLE}'")) == 0


# --------------------------------------------------------------------------- ④-1 contracts


def _dataset():
    from app.schemas.stock_research_v2 import DatasetRef
    return DatasetRef(dataset_id=uuid4(), data_version="a" * 64, qualification="observation_only")


def _factors():
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    return ResearchRequirements()


def _index():
    from app.schemas.stock_benchmark_v2 import BenchmarkSpec, IndexPoint, IndexSeries
    return IndexSeries(spec=BenchmarkSpec(instrument_id="000300.SH", kind="price_index", total_return_basis="not_applicable",
        currency="CNY", market="CN_A", timezone="Asia/Shanghai", frequency="1d", level_unit="point", anchor="next_session_open",
        provider="qa", source_reference="qa", authorization_reference="qa", methodology_reference="qa", calendar_reference="qa",
        data_version="b" * 64, qualification="unverified"),
        rows=[IndexPoint(trade_date="2026-01-05", close="10"), IndexPoint(trade_date="2026-01-06", close="11")])


def _split():
    from app.schemas.stock_ml import TimeSplit
    return TimeSplit(train_start="2026-01-05", train_end="2026-02-02", validation_start="2026-02-03",
        validation_end="2026-02-23", test_start="2026-02-24", test_end="2026-03-23")


def _walkforward():
    from app.schemas.stock_ml_experiment import WalkForwardSpec
    return {"sessions": ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"],
        "train_sessions": 2, "validation_sessions": 1, "test_sessions": 1, "fold_count": 1}


def test_task_chain_create_contracts_cover_every_kind_and_stay_bounded():
    from app.schemas.stock_research_v2_jobs import (METHOD_VERSIONS, WORKFLOW_VERSIONS, CreateBacktestJob,
        CreateBenchmarkJob, CreateDatasetJob, CreateMLTrainJob, CreateReportJob, CreateResearchICJob)
    assert set(WORKFLOW_VERSIONS) == {"stock-v2-dataset-job-v1", "stock-v2-research-task-v1"}
    assert METHOD_VERSIONS[0] == "stock-observation-manifest-v1"
    assert CreateDatasetJob.model_fields["kind"].default == "dataset"
    for model, kind in ((CreateResearchICJob, "research_ic"), (CreateBacktestJob, "backtest"), (CreateBenchmarkJob, "benchmark"),
                        (CreateMLTrainJob, "ml_train"), (CreateReportJob, "report"), (CreateDatasetJob, "dataset")):
        assert model.model_fields["kind"].default == kind, kind
        assert model.model_fields["kind"].annotation.__args__ == (kind,), kind
    # The observation dataset contract keeps refusing every other kind.
    valid = {"idempotency_key": str(uuid4()), "parameters": {"source_ids": [str(uuid4())], "start": "2026-01-05", "end": "2026-01-05",
        "warmup_start": "2026-01-05", "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"]}}
    assert CreateDatasetJob.model_validate(valid).kind == "dataset"
    for patch in ({"kind": "ml_train"}, {"max_runtime_seconds": 901}, {"max_runtime_seconds": 29}, {"max_queue_seconds": 901},
                  {"parameters": {**valid["parameters"], "expression": "delete everything"}}):
        with pytest.raises(ValidationError):
            CreateDatasetJob.model_validate({**valid, **patch})


def test_task_chain_parameters_are_discriminated_and_reject_foreign_bodies():
    from app.schemas.stock_research_v2_jobs import (CreateBacktestJob, CreateBenchmarkJob, CreateDatasetJob,
        CreateMLTrainJob, CreateReportJob, CreateResearchICJob, DatasetClaim)
    valid = {"idempotency_key": str(uuid4()), "parameters": {"source_ids": [str(uuid4())], "start": "2026-01-05", "end": "2026-01-05",
        "warmup_start": "2026-01-05", "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"]}}
    ic = CreateResearchICJob.model_validate({"idempotency_key": str(uuid4()), "parameters": {"dataset": _dataset().model_dump(mode="json"),
        "factors": _factors().model_dump(mode="json"), "horizon": 20, "groups": 5}})
    assert ic.kind == "research_ic"
    backtest = CreateBacktestJob.model_validate({"idempotency_key": str(uuid4()), "parameters": {"dataset": _dataset().model_dump(mode="json"),
        "factors": _factors().model_dump(mode="json"), "pool_version": "a" * 64, "start": "2026-01-05", "end": "2026-02-02"}})
    assert backtest.parameters.weight == "equal"
    benchmark = CreateBenchmarkJob.model_validate({"idempotency_key": str(uuid4()), "parameters": {"dataset": _dataset().model_dump(mode="json"),
        "strategy_artifact_sha256": "c" * 64, "index": _index().model_dump(mode="json")}})
    assert benchmark.kind == "benchmark"
    ml = CreateMLTrainJob.model_validate({"idempotency_key": str(uuid4()), "parameters": {"dataset": _dataset().model_dump(mode="json"),
        "factors": _factors().model_dump(mode="json"), "split": _walkforward()}})
    assert ml.parameters.training.threads == 2
    report = CreateReportJob.model_validate({"idempotency_key": str(uuid4()), "parameters": {"dataset": _dataset().model_dump(mode="json"),
        "factors": _factors().model_dump(mode="json"), "split": _split().model_dump(mode="json")}})
    assert report.kind == "report"
    # A claim never carries another kind's parameters.
    claim = {"job_id": str(uuid4()), "scope": {"owner_id": str(uuid4()), "trading_mode": "virtual"},
        "worker": {"instance_id": str(uuid4()), "token": str(uuid4())}, "token": str(uuid4()),
        "request_id": "a" * 32, "deadline_at": "2026-10-24T00:00:00Z"}
    assert DatasetClaim.model_validate({**claim, "kind": "dataset", "parameters": valid["parameters"]}).kind == "dataset"
    assert DatasetClaim.model_validate({**claim, "parameters": valid["parameters"]}).kind == "dataset"
    with pytest.raises(ValidationError):
        DatasetClaim.model_validate({**claim, "kind": "ml_train", "parameters": valid["parameters"]})
    with pytest.raises(ValidationError):
        DatasetClaim.model_validate({**claim, "kind": "research_ic", "parameters": ml.parameters.model_dump(mode="json")})


def test_dataset_job_summary_accepts_the_task_chain_kinds_but_keeps_the_observation_result_guard():
    from app.schemas.stock_dataset_jobs_api import DatasetJobSummary
    body = {"id": str(uuid4()), "status": "pending", "step": "queued", "attempts": 0, "request_id": "a" * 32,
        "created_at": "2026-10-24T00:00:00Z", "updated_at": "2026-10-24T00:00:00Z", "started_at": None, "finished_at": None,
        "deadline_at": None, "queue_expires_at": "2026-10-24T00:05:00Z", "result": None, "error_code": None}
    for kind in KINDS:
        for workflow in ("stock-v2-dataset-job-v1", "stock-v2-research-task-v1"):
            for method in ("stock-observation-manifest-v1", "stock-research-task-v1"):
                assert DatasetJobSummary.model_validate({**body, "kind": kind, "workflow_version": workflow, "method_version": method}).kind == kind
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate({**body, "kind": "unknown_kind", "workflow_version": "stock-v2-dataset-job-v1", "method_version": "stock-observation-manifest-v1"})
    # The pre-existing observation guard is untouched: a finished non-dataset task has no
    # dataset result to project yet, so the summary still refuses it instead of faking one.
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate({**body, "kind": "ml_train", "status": "completed", "attempts": 1,
            "workflow_version": "stock-v2-research-task-v1", "method_version": "stock-research-task-v1"})
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate({**body, "kind": "dataset", "status": "completed", "attempts": 1,
            "workflow_version": "stock-v2-dataset-job-v1", "method_version": "stock-observation-manifest-v1",
            "result": {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "backtest_ready"}})


# --------------------------------------------------------------------------- ④-2 mounting


def _mounted_routes(application):
    """Flatten the router tree into ``(prefixed path, route)`` pairs.

    This FastAPI build defers ``include_router`` behind ``_IncludedRouter``
    branches, so ``application.routes`` holds the included routers themselves
    rather than their flattened ``APIRoute`` objects.  Walking the branches and
    re-applying each branch's prefix keeps this test independent of that
    implementation detail while still proving the routes really are reachable.
    """
    found = []

    def walk(routes, prefix):
        for route in routes:
            branch = getattr(route, "original_router", None)
            if branch is not None:
                walk(branch.routes, prefix + (getattr(route.include_context, "prefix", "") or ""))
                continue
            found.append((prefix + getattr(route, "path", ""), route))

    walk(application.routes, "")
    return found


def test_main_app_mounts_both_previously_unmounted_v2_routers_with_the_stock_gate():
    from app.main import create_app
    application = create_app()
    mounted = _mounted_routes(application)
    paths = {(path, method.upper()) for path, route in mounted for method in getattr(route, "methods", []) or []}
    for entry in [(BASE + "/jobs/availability", "GET"), (BASE + "/jobs", "GET"), (BASE + "/jobs/{identifier}", "GET"),
                  (BASE + "/datasets/jobs", "POST"), (BASE + "/jobs/{identifier}/cancel", "POST"),
                  (BASE + "/availability", "GET"), (BASE + "/datasets", "GET"), (BASE + "/datasets/{identifier}", "GET")]:
        assert entry in paths, entry
    # Every reachable v2 route must still sit behind the stock market gate.
    gated = 0
    for path, route in mounted:
        if not path.startswith(BASE):
            continue
        dependant = getattr(route, "dependant", None)
        calls = {dependency.call.__name__ for dependency in dependant.dependencies if dependency.call}
        assert "require_stock_market" in calls or any(
            "require_stock_market" in {item.call.__name__ for item in dependency.dependencies}
            for dependency in dependant.dependencies), path
        gated += 1
    assert gated >= 8, "expected both previously unmounted routers to be reachable, got %d" % gated


def _logger():
    import logging
    return logging.getLogger("qihuo.test")


class _RefusedFactory:
    """A session factory whose readiness probe always fails, as an unapplied 021 does."""

    def __call__(self):
        raise RuntimeError("no database in this unit test")


class _ReadySession:
    """A session whose readiness probe succeeds, as a registered 021 does."""

    async def connection(self):
        return object()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exception):
        return False


class _ReadyFactory:
    def __call__(self):
        return _ReadySession()


def _ready_021(monkeypatch):
    """Stand in for the read-only 021 check with a registered, checksum-matched row."""

    async def ready(engine, *, check=True):
        assert check is True, "the lifecycle must never apply 021, only check it"
        return {"version": "20261025_021_stock_research_v2_task_chain", "checksum": "0" * 64,
                "applied": False, "checked": True}

    monkeypatch.setattr("app.main.migrate_stock_research_v2_task_chain", ready)


def test_task_chain_switch_defaults_off_and_mirrors_into_app_state(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from app.core.config import Settings
    from app.main import _start_stock_research_v2
    assert Settings.model_fields["stock_research_v2_task_chain_enabled"].default is False
    assert Settings.model_fields["stock_research_v2_artifact_path"].default == ""

    class State:
        pass

    def application(factory):
        probe = SimpleNamespace(state=State())
        probe.state.session_factory = factory
        return probe

    def settings(path=None):
        return SimpleNamespace(stock_research_v2_task_chain_enabled=True,
            stock_research_v2_artifact_path=path)

    async def closed():
        app = application(_RefusedFactory())
        await _start_stock_research_v2(app, SimpleNamespace(stock_research_v2_task_chain_enabled=False,
            stock_research_v2_artifact_path=None), _logger())
        assert app.state.stock_research_v2_task_chain_enabled is False
        assert app.state.stock_v2_jobs_enabled is False
        assert app.state.stock_research_artifact_store is None
        assert app.state.stock_research_v2_worker is None
        assert app.state.stock_research_v2_error_code is None

    async def unready():
        app = application(_RefusedFactory())
        await _start_stock_research_v2(app, settings(str(tmp_path / "artifacts")), _logger())
        assert app.state.stock_research_v2_task_chain_enabled is False
        assert app.state.stock_v2_jobs_enabled is False
        assert app.state.stock_research_v2_error_code == "v2_task_chain_migration_not_ready"
        assert app.state.stock_research_v2_worker is None
        assert app.state.stock_research_artifact_store is None

    async def no_root():
        _ready_021(monkeypatch)
        app = application(_ReadyFactory())
        await _start_stock_research_v2(app, settings(""), _logger())
        assert app.state.stock_research_v2_task_chain_enabled is True
        assert app.state.stock_v2_jobs_enabled is False
        assert app.state.stock_research_v2_error_code == "v2_task_chain_artifact_root_missing"
        assert app.state.stock_research_v2_worker is None

    async def started():
        _ready_021(monkeypatch)
        app = application(_ReadyFactory())
        await _start_stock_research_v2(app, settings(str(tmp_path / "artifacts")), _logger())
        try:
            assert app.state.stock_research_v2_task_chain_enabled is True
            assert app.state.stock_v2_jobs_enabled is True
            assert app.state.stock_research_artifact_store is not None
            assert app.state.stock_research_v2_error_code is None
        finally:
            await app.state.stock_research_v2_worker.stop()

    asyncio.run(closed())
    asyncio.run(unready())
    asyncio.run(no_root())
    asyncio.run(started())


@pytest.mark.asyncio
async def test_closed_switch_keeps_availability_negative_while_list_and_cancel_stay_usable(tmp_path):
    from app.services.stock_research_v2.jobs import acquire_worker
    from tests.test_stock_dataset_jobs_api import application as build
    from tests.test_stock_research_job_service import job_database
    from tests.test_stock_research_v2_jobs import setup
    import httpx
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "artifacts")
        app, _ = build(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            closed = await client.get(BASE + "/jobs/availability")
            assert closed.status_code == 200 and closed.json()["accepting"] is False
            assert closed.json()["reason"]
            blocked = await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))
            assert blocked.status_code == 503 and blocked.json()["detail"]["code"] == "v2_jobs_disabled"
            app.state.stock_v2_jobs_enabled = True
            async with factory() as session:
                await acquire_worker(session, uuid4())
                await session.commit()
            created = (await client.post(BASE + "/datasets/jobs", json=command.model_dump(mode="json"))).json()
            assert (await client.get(BASE + "/jobs")).json()["total"] == 1
            assert (await client.post(BASE + "/jobs/" + created["id"] + "/cancel")).json()["status"] == "cancelled"
            app.state.stock_v2_jobs_enabled = False
            assert (await client.get(BASE + "/jobs")).json()["total"] == 1


@pytest.mark.asyncio
async def test_mounted_worker_acquires_an_explicit_lease_idles_and_stops_cleanly(tmp_path):
    from app.models.stock_research_v2_job import V2JobBase, V2JobSchemaVersion
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import SCHEMA_PATH, SCHEMA_VERSION, worker_row
    from app.services.stock_research_v2.worker import StockResearchV2Worker
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    import asyncio
    import hashlib
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            session.sync.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
            V2JobBase.metadata.create_all(session.get_bind())
            session.add(V2JobSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
            await session.commit()
        worker = StockResearchV2Worker(factory, ArtifactStore(tmp_path / "artifacts"), idle_seconds=0.01)
        worker.start()
        for _ in range(300):
            await asyncio.sleep(0.01)
            async with factory() as session:
                if await worker_row(session) is not None:
                    break
        async with factory() as session:
            row = await worker_row(session)
            assert row is not None and row.instance_id == worker.instance_id
        await worker.stop()
        assert worker.task is None or worker.task.done()
        assert list((tmp_path / "artifacts").iterdir()) == []


@pytest.mark.asyncio
async def test_worker_never_steals_a_live_lease_and_takes_over_only_after_it_expires(tmp_path):
    from app.models.stock_research_v2_job import V2JobBase, V2JobSchemaVersion
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.jobs import SCHEMA_PATH, SCHEMA_VERSION, acquire_worker, worker_row
    from app.services.stock_research_v2.worker import StockResearchV2Worker
    from tests.test_stock_research_archive_v2 import prepare
    from tests.test_stock_research_job_service import job_database
    import asyncio
    import hashlib
    from datetime import timedelta
    from uuid import uuid4
    from app.services.stock_research_jobs import utc_now
    calls = []

    async def once(session_factory, store, lease, *, runner=None, columnar_store=None):
        calls.append(lease.instance_id)
        return False

    async with job_database() as (factory, owner, other):
        await prepare(factory)
        await prepare_registry(factory)
        async with factory() as session:
            await acquire_worker(session, uuid4())
            await session.commit()
        worker = StockResearchV2Worker(factory, ArtifactStore(tmp_path / "artifacts"), idle_seconds=0.01,
            backoff_seconds=0.01, once=once)
        worker.start()
        await asyncio.sleep(0.15)
        assert calls == [], "worker claimed a cycle while another instance held a live lease"
        async with factory() as session:
            row = await worker_row(session)
            assert row is not None and row.instance_id != worker.instance_id
            row.lease_expires_at = utc_now() - timedelta(seconds=1)
            await session.commit()
        for _ in range(400):
            await asyncio.sleep(0.01)
            if calls:
                break
        assert calls == [worker.instance_id], "worker did not re-earn the expired lease under its own identity"
        await worker.stop()
