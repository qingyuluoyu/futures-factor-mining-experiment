"""The two ④-3 result-path defects the full-loop test caught, pinned directly.

``test_stock_research_v2_full_loop.py`` drives the whole claim→subprocess→publish loop
and caught both of these as side effects.  Each is a defect in its own right, so each
gets a test that fails for that defect alone — otherwise a future refactor of the loop
test would silently un-pin them.
"""
from uuid import uuid4

import pytest

from tests.test_stock_benchmark_v2 import index_series, portfolio
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_task_dispatch import CURRENT_DATASET, fixed_dataset, prepare_registry, register_chain


def completed(**columns):
    """A job row carrying the result columns 021 declares, built without a database.

    ``result_reference`` reads columns off a row, so a plain model instance is enough;
    nothing here touches a session or a CHECK constraint.
    """
    from datetime import timedelta

    from app.services.stock_research_jobs import utc_now
    from app.models.stock_research_v2_job import StockResearchV2Job
    now = utc_now()
    base = dict(id=uuid4(), owner_id=uuid4(), trading_mode="virtual", idempotency_key=uuid4(),
        request_hash="a" * 64, request_id="a" * 32, kind="benchmark", status="completed", step="completed",
        parameters={"command": {}, "dataset": {}}, method_version="stock-research-task-v1",
        workflow_version="stock-v2-research-task-v1", max_runtime_seconds=300, max_queue_seconds=300, attempts=1,
        created_at=now, updated_at=now, started_at=now, finished_at=now, deadline_at=now,
        queue_expires_at=now + timedelta(seconds=300))
    base.update(columns)
    return StockResearchV2Job(**base)


def test_decay_lags_is_a_list_whether_or_not_the_caller_sent_it():
    """A field declared ``list[int]`` whose default is a tuple keeps its declared type
    only when the caller supplies it: pydantic does not validate defaults, so an omitted
    field was the sole tuple in the model and emitted a serializer warning on every dump."""
    from app.schemas.stock_research_v2_jobs import ResearchICParameters
    base = {"kind": "research_ic", "dataset": {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only"},
        "factors": {"factor_ids": ["ret_20d"], "custom_factors": [], "action": "analysis", "industry_neutral": False,
            "mktcap_neutral": False, "weight": "equal"}}
    assert ResearchICParameters.model_validate(base).decay_lags == [1, 5, 10, 20]
    assert ResearchICParameters.model_validate({**base, "decay_lags": [5, 1, 20]}).decay_lags == [1, 5, 20]


def test_each_of_the_three_021_result_shapes_renders_as_itself():
    """A research job's result is the report or experiment branch it published, never
    the dataset reference the old view() built unconditionally — which raised a
    ValidationError and turned every completed research job into a 503."""
    from app.services.stock_research_v2.jobs import result_reference
    dataset = result_reference(completed(result_dataset_id=uuid4(), result_data_version="a" * 64, result_qualification="observation_only"))
    assert dataset["qualification"] == "observation_only" and dataset["data_version"] == "a" * 64
    # The two research branches carry no dataset reference at all, so a research
    # result cannot restate — and therefore cannot claim — a dataset qualification.
    report = result_reference(completed(result_artifact_id=uuid4(), result_report_sha256="b" * 64))
    assert report == {"artifact_id": report["artifact_id"], "report_sha256": "b" * 64}
    experiment = result_reference(completed(result_experiment_id=uuid4()))
    assert set(experiment) == {"experiment_id"}
    for shape in (report, experiment):
        assert "dataset_id" not in shape and "qualification" not in shape


def test_a_non_completed_job_never_renders_a_result():
    from app.services.stock_research_v2.jobs import result_reference
    assert result_reference(completed(status="running", step="claimed")) is None
    assert result_reference(completed(status="failed", step="failed", error_code="task_inputs_unavailable")) is None


def test_a_result_column_set_no_branch_of_the_check_allows_is_refused_by_name():
    """The three branches are mutually exclusive, so a row matching none is corrupt.
    Refusing by name beats returning ``None``, which would surface far away as a vague
    "状态与结果不一致" and hide which row was broken."""
    from app.services.stock_research_v2.jobs import result_reference
    from app.services.stock_research_jobs import JobError
    # Completed, but every result column is NULL: no branch of the CHECK permits it.
    with pytest.raises(JobError) as empty:
        result_reference(completed())
    assert empty.value.code == "job_result_corrupt" and empty.value.status == 409
    # Two branches at once: also forbidden by the CHECK.
    with pytest.raises(JobError) as both:
        result_reference(completed(result_dataset_id=uuid4(), result_data_version="a" * 64,
            result_qualification="observation_only", result_report_sha256="b" * 64))
    assert both.value.code == "job_result_corrupt"
    with pytest.raises(JobError) as mixed:
        result_reference(completed(result_experiment_id=uuid4(), result_report_sha256="b" * 64))
    assert mixed.value.code == "job_result_corrupt"
    # And a non-completed row that still carries a result column.
    with pytest.raises(JobError) as stale:
        result_reference(completed(status="failed", step="failed", result_artifact_id=uuid4()))
    assert stale.value.code == "job_result_corrupt"


def test_the_job_api_accepts_each_result_shape_and_still_refuses_a_claim_of_one():
    from app.schemas.stock_dataset_jobs_api import DatasetJobSummary
    from pydantic import ValidationError
    body = {"id": str(uuid4()), "kind": "benchmark", "status": "completed", "step": "completed", "attempts": 1,
        "request_id": "a" * 32, "created_at": "2026-10-24T00:00:00Z", "updated_at": "2026-10-24T00:00:00Z",
        "started_at": "2026-10-24T00:00:00Z", "finished_at": "2026-10-24T00:01:00Z",
        "deadline_at": "2026-10-24T00:05:00Z", "queue_expires_at": "2026-10-24T00:05:00Z",
        "method_version": "stock-research-task-v1", "workflow_version": "stock-v2-research-task-v1",
        "error_code": None}
    assert DatasetJobSummary.model_validate({**body, "result": None, "status": "pending", "step": "queued", "attempts": 0,
        "started_at": None, "finished_at": None, "deadline_at": None}).result is None
    report = DatasetJobSummary.model_validate({**body, "result": {"artifact_id": str(uuid4()), "report_sha256": "b" * 64}}).result
    assert report.report_sha256 == "b" * 64
    assert DatasetJobSummary.model_validate({**body, "result": {"experiment_id": str(uuid4())}}).result.experiment_id
    # A report payload is never re-read as a dataset reference missing its columns:
    # Contract forbids extra fields, and a dataset reference needs all three of its own.
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate({**body, "result": {"report_sha256": "b" * 64}})
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate({**body, "result": {"dataset_id": str(uuid4()), "data_version": "a" * 64,
            "qualification": "research_only"}})
    # A completed job must still carry a result, and a non-completed one must not.
    with pytest.raises(ValidationError):
        DatasetJobSummary.model_validate({**body, "result": None})


@pytest.mark.asyncio
async def test_a_kernel_named_refusal_reaches_the_job_row_instead_of_worker_failed(tmp_path, monkeypatch):
    """The child reports a *named* refusal precisely so the job row can record why it
    stopped.  Flattening it to ``worker_failed`` threw away the only diagnosis an
    operator gets — and ``task_inputs_unavailable`` is the ordinary outcome for four of
    the five kinds, so this was the common case, not an edge case."""
    from app.schemas.stock_research_v2_jobs import DatasetClaim
    from app.services.stock_research_v2 import task_execution
    from app.services.stock_research_v2.task_dispatch import TASK_FAILURE_CODES
    from app.services.stock_research_jobs import JobError
    from app.services.stock_research_worker import ProcessExecutionError

    async def refused(process, **kwargs):
        raise ProcessExecutionError("task_inputs_unavailable", "内核所需的日历/股票池/列式凭证未随任务提供", 422)

    monkeypatch.setattr(task_execution, "supervise_process", refused)
    # No real interpreter: the runner still spawns, writes and cleans up, but against
    # a child that has already exited.
    monkeypatch.setattr(task_execution, "spawn_bounded_process", lambda *a, **kw: _Exited())
    async with job_database() as (factory, owner, other):
        await prepare_registry(factory)
        await register_chain(factory)
        CURRENT_DATASET[0] = await fixed_dataset(factory, owner)
        from app.services.stock_research_v2.jobs import JobService, acquire_worker, claim_next
        from app.schemas.stock_research_v2 import Scope
        from app.schemas.stock_research_v2_jobs import CreateResearchICJob
        from app.services.stock_research_v2.artifacts import ArtifactStore
        from app.services.stock_research_v2.dataset_worker import execute_claim
        from tests.test_stock_research_v2_task_dispatch import kind_parameters
        scope = Scope(owner_id=owner, trading_mode="virtual")
        async with factory() as session:
            lease = await acquire_worker(session, uuid4())
            await session.commit()
        command = CreateResearchICJob(idempotency_key=uuid4(),
            parameters={"kind": "research_ic", **kind_parameters("research_ic")})
        async with factory() as session:
            submitted = await JobService(session, scope).submit(command, request_id="d" * 32)
            claim = await claim_next(session, lease)
            await session.commit()
        store = ArtifactStore(tmp_path / "artifacts")
        with pytest.raises(JobError) as carried:
            await task_execution.supervise_research_process(_Exited(), heartbeat=_noop, timeout_seconds=5)
        assert carried.value.code == "task_inputs_unavailable" and carried.value.status == 422
        # And the parent persists it, because it is one of the registered task codes.
        assert "task_inputs_unavailable" in TASK_FAILURE_CODES
        assert await execute_claim(factory, store, claim, runner=task_execution.run_research_process) is False
        from uuid import UUID
        async with factory() as session:
            failed = await JobService(session, scope).get(UUID(submitted["id"]))
        assert failed["status"] == "failed" and failed["error_code"] == "task_inputs_unavailable"


async def _noop(*args, **kwargs):
    return True


class _Exited:
    """A child that has already exited: enough for the runner's and supervise's
    cleanup clauses, so no real interpreter is started for a unit test of the mapping."""

    def poll(self):
        return 1

    def close(self):
        return None

    stdout = None
    stdin = None


@pytest.mark.asyncio
async def test_a_code_no_claim_may_persist_is_refused_rather_than_written(tmp_path, monkeypatch):
    """Carrying the child's code through must not become a channel for arbitrary text:
    an unregistered code, including anything a kernel or a model produced, still fails
    the job as ``worker_failed``."""
    from app.services.stock_research_v2 import task_execution
    from app.services.stock_research_jobs import JobError
    from app.services.stock_research_worker import ProcessExecutionError
    for code in ("model_said_so", "task_result_published_early", "", "worker_failed: extra", "Task_Inputs_Unavailable", 7, None):
        async def refused(process, *, code=code, **kwargs):
            raise ProcessExecutionError(code, "内核自行命名的失败", 422)

        monkeypatch.setattr(task_execution, "supervise_process", refused)
        with pytest.raises(JobError) as denied:
            await task_execution.supervise_research_process(_Exited(), heartbeat=_noop, timeout_seconds=5)
        assert denied.value.code == "worker_failed", code
