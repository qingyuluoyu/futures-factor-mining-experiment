"""④-3: kind dispatch, a bounded research subprocess, and the two new result shapes.

Every test here is written against the contract 021 declares rather than against an
implementation: an unregistered kind is refused by name and never falls back to the
observation module, a research claim is executed by its own bounded subprocess, and
``finish_experiment`` / ``finish_report`` each write exactly one of the three
mutually exclusive result shapes that ``ck_stock_v2_job_result`` allows.
"""
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.services.stock_research_jobs import JobError, utc_now
from tests.test_stock_benchmark_v2 import index_series
from tests.test_stock_research_job_service import job_database

TASK_MODULE = "app.services.stock_research_v2.research_process"
DATASET_MODULE = "app.services.stock_research_v2.dataset_process"
CURRENT_DATASET = [None]


async def prepare_registry(factory):
    """Every table the 021 result shapes reference, plus the 016 version row."""
    from app.models.stock_ml_registry import MLRegistryBase
    from app.models.stock_research_dataset import DatasetBase
    from app.models.stock_research_v2_job import V2JobBase, V2JobSchemaVersion
    from app.services.stock_research_v2.jobs import SCHEMA_PATH, SCHEMA_VERSION
    from tests.test_stock_research_archive_v2 import prepare
    await prepare(factory)
    async with factory() as session:
        session.sync.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
        DatasetBase.metadata.create_all(session.get_bind())
        V2JobBase.metadata.create_all(session.get_bind())
        MLRegistryBase.metadata.create_all(session.get_bind())
        session.add(V2JobSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()


async def register_chain(factory):
    """Register 021 on a throwaway engine so the research path becomes reachable."""
    from app.services.stock_research_v2.task_dispatch import CHAIN_PATH, CHAIN_TABLE, CHAIN_VERSION
    async with factory() as session:
        session.sync.connection().exec_driver_sql(f"CREATE TABLE IF NOT EXISTS {CHAIN_TABLE} "
            "(version VARCHAR(64) PRIMARY KEY, checksum VARCHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        session.sync.connection().exec_driver_sql(f"INSERT INTO {CHAIN_TABLE} (version, checksum) VALUES (?, ?)",
            (CHAIN_VERSION, hashlib.sha256(CHAIN_PATH.read_bytes()).hexdigest()))
        await session.commit()


async def fixed_dataset(factory, owner, *, data_version="a" * 64, qualification="observation_only"):
    """A minimal owned, ready dataset row the research parameters can reference."""
    from app.models.stock_research_dataset import StockResearchArtifact, StockResearchDataset
    async with factory() as session:
        artifact = StockResearchArtifact(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
            request_hash="e" * 64, request_id="f" * 32, sha256=data_version, byte_size=32, relative_path=data_version + ".json",
            purpose="dataset", state="ready")
        session.add(artifact)
        await session.flush()
        dataset = StockResearchDataset(id=uuid4(), manifest_artifact_id=artifact.id, data_version=data_version, qualification=qualification)
        session.add(dataset)
        await session.commit()
        return dataset.id


def research_claim(**overrides):
    from datetime import timedelta
    from app.schemas.stock_research_v2_jobs import DatasetClaim
    body = {"job_id": str(uuid4()), "kind": "research_ic",
        "scope": {"owner_id": str(uuid4()), "trading_mode": "virtual"},
        "worker": {"instance_id": str(uuid4()), "token": str(uuid4()), "workflow_version": "stock-v2-dataset-job-v1"},
        "token": str(uuid4()), "request_id": "a" * 32, "deadline_at": (utc_now() + timedelta(seconds=120)).isoformat(),
        "parameters": {"kind": "research_ic", "dataset": {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only"},
            "factors": {"factor_ids": ["ret_20d"], "custom_factors": []}, "horizon": 5, "groups": 5}}
    body.update(overrides)
    return DatasetClaim.model_validate(body)


def kind_parameters(kind, qualification="observation_only"):
    """The minimal payload each kind's own contract declares.

    A claim can never carry another kind's body, so the per-kind refusal test has to
    build a *valid* payload per kind and then watch the kernel refuse it for the
    evidence it does not have — never an invalid payload that the schema rejects
    first, which would prove nothing about dispatch.
    """
    reference = {"dataset_id": str(CURRENT_DATASET[0] or uuid4()), "data_version": "a" * 64, "qualification": qualification}
    factors = {"factor_ids": ["ret_20d"], "custom_factors": []}
    sessions = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"]
    walk = {"sessions": sessions, "train_sessions": 3, "validation_sessions": 2, "test_sessions": 2, "fold_count": 1}
    window = {"train_start": "2026-01-05", "train_end": "2026-01-07", "validation_start": "2026-01-08",
        "validation_end": "2026-01-09", "test_start": "2026-01-12", "test_end": "2026-01-13"}
    return {"research_ic": {"dataset": reference, "factors": factors},
        "backtest": {"dataset": reference, "factors": factors, "pool_version": "a" * 64, "start": "2026-01-05", "end": "2026-01-06"},
        "benchmark": {"dataset": reference, "strategy_artifact_sha256": "a" * 64, "index": index_series().model_dump(mode="json")},
        "ml_train": {"dataset": reference, "factors": factors, "split": walk},
        "report": {"dataset": reference, "factors": factors, "split": window}}[kind]


async def submit_task(session, scope, kind, qualification="observation_only"):
    """Submit one job of ``kind`` against the current fixed dataset."""
    from app.schemas.stock_research_v2_jobs import (CreateBacktestJob, CreateBenchmarkJob, CreateMLTrainJob, CreateReportJob,
        CreateResearchICJob)
    from app.services.stock_research_v2.jobs import JobService
    models = {"research_ic": CreateResearchICJob, "backtest": CreateBacktestJob, "benchmark": CreateBenchmarkJob,
        "ml_train": CreateMLTrainJob, "report": CreateReportJob}
    command = models[kind](idempotency_key=uuid4(), parameters={"kind": kind, **kind_parameters(kind, qualification)})
    return await JobService(session, scope).submit(command, request_id="b" * 32)


async def submit_research(session, scope, *, data_version="a" * 64):
    from app.schemas.stock_research_v2_jobs import CreateResearchICJob, ResearchICParameters
    from app.services.stock_research_v2.jobs import JobService
    command = CreateResearchICJob(idempotency_key=uuid4(), parameters=ResearchICParameters.model_validate(
        {"kind": "research_ic", "dataset": {"dataset_id": str(CURRENT_DATASET[0]), "data_version": data_version, "qualification": "observation_only"},
            "factors": {"factor_ids": ["ret_20d"], "custom_factors": []}, "horizon": 5, "groups": 5}))
    return await JobService(session, scope).submit(command, request_id="b" * 32)


@pytest.mark.asyncio
async def test_unregistered_kind_is_refused_by_name_and_never_falls_back_to_dataset():
    from app.schemas.stock_research_v2_jobs import DatasetClaim
    from app.services.stock_research_v2.research_process import TASK_KERNELS
    from app.services.stock_research_v2.task_dispatch import TASK_MODULES, task_module, task_shape
    # The registry is closed: only the five research kinds are dispatchable, and the
    # observation dataset kind is deliberately absent so nothing can rewrite one
    # kernel into another.
    assert set(TASK_MODULES) == {"research_ic", "backtest", "benchmark", "ml_train", "report"}
    assert set(TASK_KERNELS) == set(TASK_MODULES)
    assert "dataset" not in TASK_MODULES and "dataset" not in TASK_KERNELS
    for kind in ("research_ic", "backtest", "benchmark", "ml_train", "report"):
        assert task_module(kind) == TASK_MODULE and task_shape(kind) in {"experiment", "report"}
    for kind in ("", "dataset", "DATASET", "research", "ml", "report_v2", "drop table", None, 7):
        with pytest.raises(JobError) as refused:
            task_module(kind)
        assert refused.value.code == "task_kind_unregistered", kind
        assert refused.value.status == 422
        with pytest.raises(JobError) as also:
            task_shape(kind)
        assert also.value.code == "task_kind_unregistered", kind
    # A claim can never carry another kind's payload, so a swapped body is refused
    # by the schema before any dispatch decision is made.
    claim = research_claim()
    with pytest.raises(ValueError):
        DatasetClaim.model_validate({**claim.model_dump(mode="json"), "kind": "benchmark"})
    with pytest.raises(ValueError):
        DatasetClaim.model_validate({**claim.model_dump(mode="json"),
            "parameters": {**claim.parameters.model_dump(mode="json"), "kind": "ml_train"}})


@pytest.mark.asyncio
async def test_claim_next_routes_by_persisted_kind_and_refuses_a_swapped_payload():
    from app.models.stock_research_v2_job import StockResearchV2Job
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
        scope = Scope(owner_id=owner, trading_mode="virtual")
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            created = await submit_research(session, scope)
            assert created["kind"] == "research_ic"
            assert created["workflow_version"] == "stock-v2-research-task-v1"
            assert created["method_version"] == "stock-research-task-v1"
            await session.commit()
            claim = await claim_next(session, lease)
            assert claim is not None and claim.kind == "research_ic"
            assert claim.parameters.kind == "research_ic" and claim.parameters.dataset.dataset_id == CURRENT_DATASET[0]
            # One running claim per lease; a second one is not handed out.
            assert await claim_next(session, lease) is None
            await session.commit()
        # A persisted payload swapped for another kind's body is refused by name and
        # fails that job; it is never re-dispatched as the kind it now claims.  The
        # swap happens before the claim, because a running claim is never re-read.
        async with job_database() as (factory, owner, other):
            await prepare_registry(factory)
            await register_chain(factory)
            CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
            scope = Scope(owner_id=owner, trading_mode="virtual")
            async with factory() as session:
                lease = await acquire_worker(session, uuid4())
                await submit_research(session, scope)
                await session.commit()
            async with factory() as session:
                job = (await session.execute(select(StockResearchV2Job).where(StockResearchV2Job.kind == "research_ic"))).scalar_one()
                # Rebind the whole document: an in-place edit of a JSON column is
                # invisible to the unit of work, so the swap would never be persisted.
                job.parameters = {**job.parameters, "command": {"kind": "backtest", "dataset": {"dataset_id": str(CURRENT_DATASET[0]),
                    "data_version": "a" * 64, "qualification": "observation_only"},
                    "factors": {"factor_ids": ["ret_20d"], "custom_factors": []}, "pool_version": "a" * 64,
                    "start": "2026-01-05", "end": "2026-01-06", "qa_only": True}}
                await session.commit()
            async with factory() as session:
                assert await claim_next(session, lease) is None
                failed = (await JobService(session, scope).get(job.id))
                assert failed["status"] == "failed" and failed["error_code"] == "job_input_integrity_failed"
                await session.commit()
        # Without 021 registered, no research claim is handed out at all.
        async with job_database() as (factory, owner, other):
            await prepare_registry(factory)
            CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
            scope = Scope(owner_id=owner, trading_mode="virtual")
            async with factory() as session:
                lease = await acquire_worker(session, uuid4())
                await submit_research(session, scope)
                assert await claim_next(session, lease) is None
                failed = (await JobService(session, scope).list(limit=10, offset=0))["items"][0]
                assert failed["status"] == "failed" and failed["error_code"] == "task_chain_not_ready"
                await session.commit()


@pytest.mark.asyncio
async def test_finish_experiment_and_finish_report_write_exactly_one_result_shape(tmp_path):
    from app.models.stock_ml_registry import StockMLExperiment
    from app.models.stock_research_dataset import StockResearchArtifact
    from app.models.stock_research_v2_job import StockResearchV2Job
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.archive import descriptor
    from app.services.stock_research_v2.jobs import acquire_worker, claim_next, finish_experiment, finish_report, release_claim
    from tests.test_stock_research_v2_task_chain import RELAXED, RESULT_COLUMNS
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        # An experiment is an ``ml`` result, and its evidence floor is research_only:
        # an observation-only dataset can never publish one, which is the same
        # fail-closed rule the qualification gate enforces everywhere else.
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner, qualification="research_only")
        store = ArtifactStore(tmp_path / "artifacts")
        scope = Scope(owner_id=owner, trading_mode="virtual")
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            await submit_task(session, scope, "ml_train", "research_only")
            trained = await claim_next(session, lease)
            assert trained is not None and trained.kind == "ml_train"
            await session.commit()
        # The experiment shape points at a 019 row that already exists.
        async with factory() as session:
            experiment = StockMLExperiment(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_id="c" * 32, dataset_id=CURRENT_DATASET[0], data_version="a" * 64,
                report_sha256="b" * 64, report_byte_size=128, state="ready", validation_status="research_only", fold_count=1, detail_json="{}")
            session.add(experiment)
            await session.commit()
            experiment_id = experiment.id
        async with factory() as session:
            # Only the experiment shape is publishable for this kind, and only once.
            with pytest.raises(JobError) as wrong_shape:
                await finish_report(session, trained, uuid4(), "c" * 64)
            assert wrong_shape.value.code == "invalid_result_shape" and wrong_shape.value.status == 409
            assert await finish_experiment(session, trained, experiment_id, "b" * 64) is True
            await session.commit()
            job = (await session.execute(select(StockResearchV2Job).where(StockResearchV2Job.id == trained.job_id))).scalar_one()
            assert job.status == "completed" and job.step == "completed"
            assert job.result_experiment_id == experiment_id
            # The other two shapes stay empty, and so do the observation columns.
            assert job.result_report_sha256 is None and job.result_artifact_id is None
            assert job.result_dataset_id is None and job.result_data_version is None and job.result_qualification is None
            # A completed claim cannot publish a second time.
            assert await finish_experiment(session, trained, experiment_id, "b" * 64) is False
            assert await finish_report(session, trained, uuid4(), "c" * 64) is False
            await session.commit()
            assert (await session.execute(select(StockResearchV2Job).where(StockResearchV2Job.id == trained.job_id))).scalar_one().result_report_sha256 is None
        # The report shape points at a 015 artifact row owned by the same scope.
        async with factory() as session:
            digest, size = descriptor({"kind": "report", "report": {"qa": True}}, store.max_bytes)
            artifact = StockResearchArtifact(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_hash="d" * 64, request_id="e" * 32, sha256=digest, byte_size=size,
                relative_path=digest + ".json", purpose="dataset", state="ready")
            session.add(artifact)
            await session.commit()
            artifact_id = artifact.id
        async with factory() as session:
            await release_claim(session, trained)
            await session.commit()
        async with factory() as session:
            await submit_task(session, scope, "research_ic", "research_only")
            second = await claim_next(session, lease)
            assert second is not None and second.kind == "research_ic"
            await session.commit()
        async with factory() as session:
            # The report kind is the mirror image: no experiment may ever be written.
            with pytest.raises(JobError) as wrong_shape:
                await finish_experiment(session, second, experiment_id, "b" * 64)
            assert wrong_shape.value.code == "invalid_result_shape" and wrong_shape.value.status == 409
            assert await finish_report(session, second, artifact_id, digest) is True
            await session.commit()
            job = (await session.execute(select(StockResearchV2Job).where(StockResearchV2Job.id == second.job_id))).scalar_one()
            assert job.status == "completed"
            assert job.result_artifact_id == artifact_id and job.result_report_sha256 == digest
            assert job.result_experiment_id is None
            assert job.result_dataset_id is None and job.result_data_version is None and job.result_qualification is None
        # 021's CHECK really is in force on this engine, so the exclusivity asserted
        # above is the database's rule and not only this module's.
        async with factory() as session:
            rows = session.sync.connection().exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name='stock_research_v2_jobs'").fetchall()
            checks = {}
            for _, sql in rows:
                for name in RELAXED:
                    marker = f"CONSTRAINT {name} CHECK ("
                    if marker in sql:
                        checks[name] = sql.split(marker, 1)[1].rsplit(")", 1)[0]
            # The job table carries the three of 021's four relaxed checks that belong
            # to it; the worker-name one lives on the worker table.
            assert set(checks) == set(RELAXED) - {"ck_stock_v2_worker_name"}
            result = checks["ck_stock_v2_job_result"]
            for column in RESULT_COLUMNS:
                assert result.count(column) >= 2, column
            # Exactly three completed branches and one non-completed branch.
            assert result.count("status = 'completed'") == 3 and result.count("status <> 'completed'") == 1
            # The observation shape still demands observation_only and clears the rest.
            assert "result_qualification = 'observation_only'" in result


@pytest.mark.asyncio
async def test_result_check_rejects_two_shapes_at_once_and_a_strange_report_length():
    from app.models.stock_ml_registry import StockMLExperiment
    from app.models.stock_research_dataset import StockResearchArtifact
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next, fail_claim, release_claim
    from sqlalchemy.exc import IntegrityError
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
        scope = Scope(owner_id=owner, trading_mode="virtual")
        # Both referenced rows exist, so only 021's result CHECK can reject a write
        # below: a foreign-key failure would prove nothing about the shape rule.
        async with factory() as session:
            experiment = StockMLExperiment(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_id="c" * 32, dataset_id=CURRENT_DATASET[0], data_version="a" * 64,
                report_sha256="b" * 64, report_byte_size=128, state="ready", validation_status="qa_only", fold_count=1, detail_json="{}")
            artifact = StockResearchArtifact(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_hash="d" * 64, request_id="e" * 32, sha256="c" * 64, byte_size=16,
                relative_path="c" * 64 + ".json", purpose="dataset", state="ready")
            session.add(experiment)
            session.add(artifact)
            await session.commit()
            experiment_id, artifact_id = experiment.id, artifact.id
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            await submit_research(session, scope)
            claim = await claim_next(session, lease)
            assert claim is not None
            await session.commit()
        dataset_id = claim.parameters.dataset.dataset_id
        for values in (
            # Two shapes at once, in both directions.
            {"result_dataset_id": dataset_id, "result_data_version": "a" * 64, "result_qualification": "observation_only", "result_experiment_id": experiment_id},
            {"result_experiment_id": experiment_id, "result_report_sha256": "b" * 64, "result_artifact_id": artifact_id},
            # A report with no artifact, and an artifact with no digest.
            {"result_report_sha256": "b" * 64},
            {"result_artifact_id": artifact_id},
            # A digest of the wrong length.
            {"result_artifact_id": artifact_id, "result_report_sha256": "short"},
            # The observation shape may only ever be observation_only.
            {"result_dataset_id": dataset_id, "result_data_version": "a" * 64, "result_qualification": "research_only"},
        ):
            async with factory() as session:
                columns = ", ".join(f"{name}='{value}'" for name, value in values.items())
                with pytest.raises(IntegrityError):
                    session.sync.execute(text(f"UPDATE stock_research_v2_jobs SET status='completed', {columns} WHERE id='{claim.job_id.hex}'"))
                await session.rollback()
        # None of the rejected writes left a completed row behind, and the claim is
        # still running, so it has to be failed before its slot can be released.
        async with factory() as session:
            assert (await JobService(session, scope).get(claim.job_id))["status"] == "running"
            assert await fail_claim(session, claim, "worker_failed") is True
            await release_claim(session, claim)
            await session.commit()


@pytest.mark.asyncio
async def test_finish_experiment_and_finish_report_reject_a_foreign_or_absent_reference():
    from app.models.stock_ml_registry import StockMLExperiment
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.jobs import acquire_worker, claim_next, fail_claim, finish_experiment, finish_report, release_claim
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner, qualification="research_only")
        scope = Scope(owner_id=owner, trading_mode="virtual")
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            await submit_task(session, scope, "ml_train", "research_only")
            claim = await claim_next(session, lease)
            assert claim is not None and claim.kind == "ml_train"
            await session.commit()
        async with factory() as session:
            # An experiment that does not exist at all is a 404.  The report path is
            # not this kind's shape at all, so it is refused by shape first.
            with pytest.raises(JobError) as missing:
                await finish_experiment(session, claim, uuid4(), "b" * 64)
            assert missing.value.code == "ml_model_not_found" and missing.value.status == 404
            with pytest.raises(JobError) as wrong_shape:
                await finish_report(session, claim, uuid4(), "abc")
            assert wrong_shape.value.code == "invalid_result_shape" and wrong_shape.value.status == 409
            await session.rollback()
        # An experiment bound to a different dataset than the claim cannot be published.
        async with factory() as session:
            elsewhere = await fixed_dataset(factory, owner)
            await session.commit()
        async with factory() as session:
            foreign = StockMLExperiment(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_id="c" * 32, dataset_id=elsewhere, data_version="a" * 64,
                report_sha256="b" * 64, report_byte_size=128, state="ready", validation_status="research_only", fold_count=1, detail_json="{}")
            session.add(foreign)
            await session.commit()
            with pytest.raises(JobError) as bound:
                await finish_experiment(session, claim, foreign.id, "b" * 64)
            assert bound.value.code == "job_result_mismatch"
            await session.rollback()
        # A grade that does not match what the claim's own dataset evidence reaches is
        # never published, in either direction: the experiment may not certify itself.
        async with factory() as session:
            under = StockMLExperiment(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_id="c" * 32, dataset_id=CURRENT_DATASET[0], data_version="a" * 64,
                report_sha256="b" * 64, report_byte_size=128, state="ready", validation_status="qa_only", fold_count=1, detail_json="{}")
            session.add(under)
            await session.commit()
            with pytest.raises(JobError) as over_refused:
                await finish_experiment(session, claim, under.id, "b" * 64)
            assert over_refused.value.code == "job_result_mismatch"
            await session.rollback()
        # A digest that does not match the experiment's own report is never published.
        async with factory() as session:
            mismatched = StockMLExperiment(id=uuid4(), owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
                request_id="c" * 32, dataset_id=CURRENT_DATASET[0], data_version="a" * 64,
                report_sha256="d" * 64, report_byte_size=128, state="ready", validation_status="research_only", fold_count=1, detail_json="{}")
            session.add(mismatched)
            await session.commit()
            with pytest.raises(JobError) as digest:
                await finish_experiment(session, claim, mismatched.id, "b" * 64)
            assert digest.value.code == "job_result_mismatch"
            await session.rollback()
        # An artifact owned by somebody else is never published either, and neither
        # is one whose content does not match the reported digest.
        async with factory() as session:
            assert await fail_claim(session, claim, "worker_failed") is True
            await release_claim(session, claim)
            await submit_task(session, scope, "research_ic", "research_only")
            reporting = await claim_next(session, lease)
            assert reporting is not None and reporting.kind == "research_ic"
            await session.commit()
        async with factory() as session:
            from app.models.stock_research_dataset import StockResearchArtifact
            stranger = StockResearchArtifact(id=uuid4(), owner_id=other, trading_mode="virtual", idempotency_key=uuid4(),
                request_hash="e" * 64, request_id="f" * 32, sha256="c" * 64, byte_size=16,
                relative_path="c" * 64 + ".json", purpose="dataset", state="ready")
            session.add(stranger)
            await session.commit()
            with pytest.raises(JobError) as short:
                await finish_report(session, reporting, stranger.id, "abc")
            assert short.value.code == "invalid_result_reference" and short.value.status == 422
            with pytest.raises(JobError) as not_owned:
                await finish_report(session, reporting, stranger.id, "c" * 64)
            assert not_owned.value.code in {"artifact_not_found", "job_result_mismatch"}
            await session.rollback()
        async with factory() as session:
            assert await fail_claim(session, reporting, "worker_failed") is True
            await release_claim(session, reporting)
            await session.commit()


@pytest.mark.asyncio
async def test_fail_claim_accepts_the_task_codes_and_still_refuses_arbitrary_text():
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next, fail_claim, release_claim
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
        scope = Scope(owner_id=owner, trading_mode="virtual")
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            await session.commit()
        # Every registered code — the four the dispatched kernels may report plus the
        # two observation codes a shared failure path may still raise — persists and
        # is readable back, each on its own fresh claim because a claim is terminal.
        for code in ("task_kind_unregistered", "task_inputs_unavailable", "task_kernel_failed", "execution_timeout", "worker_failed", "dataset_invalid"):
            async with factory() as session:
                await submit_research(session, scope)
                claim = await claim_next(session, lease)
                assert claim is not None and claim.kind == "research_ic"
                assert await fail_claim(session, claim, code) is True
                await session.commit()
                assert (await JobService(session, scope).get(claim.job_id))["error_code"] == code
                assert await release_claim(session, claim) is True
                await session.commit()
        # Free-form text, including anything a kernel or a model might have produced,
        # is refused before the job row is touched at all.
        async with factory() as session:
            await submit_research(session, scope)
            claim = await claim_next(session, lease)
            await session.commit()
        async with factory() as session:
            for text in ("task_kernel_failed: model said so", "task_result_published_early", "", "anything"):
                with pytest.raises(JobError) as denied:
                    await fail_claim(session, claim, text)
                assert denied.value.code == "invalid_failure_code"
            await session.rollback()
            assert (await JobService(session, scope).get(claim.job_id))["status"] == "running"
        async with factory() as session:
            assert await fail_claim(session, claim, "worker_failed") is True
            await release_claim(session, claim)
            await session.commit()


@pytest.mark.asyncio
async def test_research_claim_runs_in_its_own_bounded_subprocess(tmp_path, monkeypatch):
    import sys
    from app.schemas.stock_research_v2_jobs import DatasetClaim
    from app.services.stock_research_v2 import task_execution
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / "artifacts")
    claim = research_claim()
    captured = {}

    class Sentinel(Exception):
        pass

    def fake_spawn(argv, *, cwd, env):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["env"] = env
        raise Sentinel()

    monkeypatch.setattr(task_execution, "spawn_bounded_process", fake_spawn)
    with pytest.raises(Sentinel):
        await task_execution.run_research_process(claim, store, None)
    # The dispatched kernel is its own program, started through the same atomic
    # Windows job boundary the observation module uses, and never that module.
    assert captured["argv"] == [sys.executable, "-u", "-m", TASK_MODULE]
    assert captured["env"]["WENYUAN_STOCK_DATASET_ROOT"] == str(store.root)
    # Native BLAS pools are pinned to one thread so a kernel cannot escape its budget.
    assert captured["env"]["OMP_NUM_THREADS"] == "1" and captured["env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert TASK_MODULE != DATASET_MODULE
    # A claim whose kind has no module is refused before any process exists.
    observation = DatasetClaim.model_validate({**claim.model_dump(mode="json"), "kind": "dataset",
        "parameters": {"source_ids": [str(uuid4())], "start": "2026-01-05", "end": "2026-01-05", "warmup_start": "2026-01-05",
            "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"]}})
    with pytest.raises(JobError) as unregistered:
        await task_execution.run_research_process(observation, store, None)
    assert unregistered.value.code == "task_kind_unregistered"
    assert captured["argv"][-1] == TASK_MODULE

    # A runner that yields no outcome fails the claim instead of publishing a shape.
    from app.schemas.stock_research_v2 import Scope
    from app.services.stock_research_v2.dataset_worker import execute_claim
    from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next, release_claim
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
        scope = Scope(owner_id=owner, trading_mode="virtual")
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            await submit_research(session, scope)
            live = await claim_next(session, lease)
            assert live is not None and live.kind == "research_ic"
            await session.commit()
        seen = {}

        async def empty_runner(inner, root, heartbeat):
            seen["kind"] = inner.kind
            return None

        written = await execute_claim(factory, store, live, runner=empty_runner)
        assert seen["kind"] == "research_ic"
        assert written is False, "no outcome must fail the claim, never publish a shape"
        async with factory() as session:
            assert (await JobService(session, scope).get(live.job_id))["error_code"] == "worker_failed"
            await release_claim(session, live)
            await session.commit()


@pytest.mark.asyncio
async def test_benchmark_kernel_compares_an_owned_artifact_and_refuses_a_foreign_one(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.research_process import run_task_kernel
    from tests.test_stock_benchmark_v2 import index_series, portfolio
    store = ArtifactStore(tmp_path / "artifacts")
    stored = store.put_json(portfolio().model_dump(mode="json"))
    claim = research_claim(kind="benchmark", parameters={"kind": "benchmark",
        "dataset": {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only"},
        "strategy_artifact_sha256": stored.sha256, "index": index_series().model_dump(mode="json")})
    # The comparison is pure calculation over already-stored, content-addressed
    # inputs: no database session, no model call, no new network access.
    outcome = run_task_kernel(claim, store)
    assert outcome["kind"] == "benchmark" and outcome["shape"] == "report"
    assert len(outcome["report_sha256"]) == 64 and outcome["report_byte_size"] > 0
    assert store.read_json(outcome["report_sha256"])["validation_status"] == "qa_only"
    # A strategy artifact that is not in this store is refused by name.
    missing = research_claim(kind="benchmark", parameters={"kind": "benchmark",
        "dataset": {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only"},
        "strategy_artifact_sha256": "f" * 64, "index": index_series().model_dump(mode="json")})
    with pytest.raises(JobError) as absent:
        run_task_kernel(missing, store)
    assert absent.value.code == "task_inputs_unavailable"


@pytest.mark.asyncio
async def test_kernels_without_their_required_evidence_refuse_instead_of_fabricating_a_result(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.research_process import run_task_kernel
    store = ArtifactStore(tmp_path / "artifacts")
    for kind in ("research_ic", "backtest", "ml_train", "report"):
        claim = research_claim(kind=kind, parameters={"kind": kind, **kind_parameters(kind)})
        with pytest.raises(JobError) as refused:
            run_task_kernel(claim, store)
        assert refused.value.code == "task_inputs_unavailable", kind
        assert refused.value.status == 422
    # Nothing was written for any of them.
    assert list(store.root.iterdir()) == []


@pytest.mark.asyncio
async def test_dataset_claims_keep_their_original_module_and_the_worker_cycle_is_unchanged(tmp_path):
    from app.services.stock_research_v2 import dataset_worker, task_execution
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_v2.dataset_worker import claim_runner, run_claim_process
    from app.services.stock_research_v2.worker import StockResearchV2Worker
    store = ArtifactStore(tmp_path / "artifacts")
    observation = research_claim(kind="dataset", parameters={"source_ids": [str(uuid4())], "start": "2026-01-05",
        "end": "2026-01-05", "warmup_start": "2026-01-05", "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"]})
    research = research_claim()
    # The observation claim still selects the observation module, with and without a
    # columnar store; a research claim selects the research module.
    assert claim_runner(observation) is run_claim_process
    assert claim_runner(research) is task_execution.run_research_process

    class Columnar:
        root = store.root

        def is_dir(self):
            return True

    assert claim_runner(observation, columnar_store=Columnar()).func is run_claim_process
    assert claim_runner(research, columnar_store=Columnar()) is task_execution.run_research_process
    # The default worker cycle is untouched: it still calls the observation entry point.
    assert StockResearchV2Worker(lambda: None, store).once is dataset_worker.run_dataset_once
    with pytest.raises(JobError):
        StockResearchV2Worker(lambda: None, store, idle_seconds=0)


@pytest.mark.asyncio
async def test_no_write_path_is_reachable_outside_the_new_switch():
    import inspect
    from app.api import stock_dataset_jobs
    from app.core.config import Settings
    from app.schemas.stock_research_v2_jobs import CreateDatasetJob
    from app.services.stock_research_v2.jobs import jobs_ready
    from app.services.stock_research_v2.task_dispatch import chain_ready
    assert Settings.model_fields["stock_research_v2_task_chain_enabled"].default is False
    assert Settings.model_fields["stock_research_v2_artifact_path"].default == ""
    # The observation write entry point still accepts the observation contract alone.
    assert inspect.signature(stock_dataset_jobs.submit).parameters["command"].annotation is CreateDatasetJob
    # Every mounted research write route is gated on the one executor switch, and each
    # of them refuses the other kinds' bodies at the schema layer, so no route can be
    # repointed at a different kernel.  Nothing here adds a second switch.
    gated = [name for name in ("submit_ic", "submit_backtest", "submit_benchmark", "submit_ml", "submit_report")
        if hasattr(stock_dataset_jobs, name)]
    assert len(gated) == 5, gated
    for name in gated:
        source = inspect.getsource(getattr(stock_dataset_jobs, name))
        assert 'stock_v2_jobs_enabled' in source, name
        assert "RESEARCH_CLOSED" in source or "v2_jobs_disabled" in source, name
    for name, model in zip(gated, ("CreateResearchICJob", "CreateBacktestJob", "CreateBenchmarkJob", "CreateMLTrainJob", "CreateReportJob")):
        assert inspect.signature(getattr(stock_dataset_jobs, name)).parameters["command"].annotation.__name__ == model, name
    # With 021 unapplied there is no readiness, so no research claim can be taken.
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        async with factory() as session:
            assert await jobs_ready(session) is True
            assert await chain_ready(session) is False
        await register_chain(factory)
        async with factory() as session:
            assert await chain_ready(session) is True
