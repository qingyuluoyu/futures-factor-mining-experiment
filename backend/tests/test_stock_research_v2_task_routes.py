"""HTTP surface for the six v2 task kinds.

The observation ``dataset`` route already existed behind its default-OFF switch.
This module pins the five research routes that migration 021 made storable: each
one is a separate path with its own command schema, so a kind can never be
reached by posting another kind's body, and every one of them stays closed until
the same explicit switch that starts the v2 executor is turned on.

Cancel is deliberately *not* gated, matching the observation route: cancelling a
job creates nothing and must keep working while new submissions are closed.
"""
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy import select
import httpx
import pytest

from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_v2_jobs import setup

BASE = "/api/stock/research/v2"
# Every research path, paired with the only command schema it accepts.
ROUTES = (("/ic/jobs", "research_ic"), ("/backtest/jobs", "backtest"), ("/benchmark/jobs", "benchmark"),
    ("/ml/jobs", "ml_train"), ("/report/jobs", "report"))


def application(factory, owner):
    from app.api.stock_dataset_jobs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/stock")
    context = SimpleNamespace(user=SimpleNamespace(id=owner, market_type="stock"), mode="virtual")
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(context.user, context.mode)

    async def sessions():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = sessions
    return app, context


from app.core.deps import AuthContext, get_auth_context, get_session  # noqa: E402  (kept beside its only use)


def command(kind, dataset, *, key=None, **extra):
    """One valid create-body per kind, built from the persisted dataset reference.

    Only the requested kind is constructed, so a per-kind override such as
    ``horizon`` never has to be accepted by the other four parameter models.  The
    command models are frozen, so an idempotency key is passed in rather than
    assigned afterwards.
    """
    from tests.test_stock_research_v2_task_chain import _factors, _index, _split, _walkforward
    from app.schemas.stock_research_v2_jobs import (CreateBacktestJob, CreateBenchmarkJob, CreateDatasetJob, CreateMLTrainJob,
        CreateReportJob, CreateResearchICJob)
    reference = {"dataset_id": str(dataset[0]), "data_version": dataset[1], "qualification": dataset[2]}
    shared = {"dataset": reference, "factors": _factors().model_dump(mode="json")}
    key = key or uuid4()
    bodies = {
        "dataset": lambda: CreateDatasetJob(idempotency_key=key, parameters={"source_ids": [str(uuid4())],
            "start": "2026-01-05", "end": "2026-01-05", "warmup_start": "2026-01-05", "pool_id": "qa",
            "pool_version": "a" * 64, "instruments": ["000001.SZ"]}),
        "research_ic": lambda: CreateResearchICJob(idempotency_key=key, parameters={**shared, **extra}),
        "backtest": lambda: CreateBacktestJob(idempotency_key=key, parameters={**shared, "pool_version": "a" * 64,
            "start": "2026-01-05", "end": "2026-02-02", **extra}),
        "benchmark": lambda: CreateBenchmarkJob(idempotency_key=key, parameters={"dataset": reference,
            "strategy_artifact_sha256": "c" * 64, "index": _index().model_dump(mode="json"), **extra}),
        "ml_train": lambda: CreateMLTrainJob(idempotency_key=key, parameters={**shared, "split": _walkforward(), **extra}),
        "report": lambda: CreateReportJob(idempotency_key=key, parameters={**shared, "split": _split().model_dump(mode="json"), **extra}),
    }
    if kind not in bodies:
        raise AssertionError(f"test helper has no body for kind {kind!r}")
    return bodies[kind]()


async def ensure_dataset_schema(factory):
    """Register the dataset and 021 task-chain schema versions once.

    Both are needed: a research job is bound to a fixed dataset row *and* stored in
    the widened job table, so the row-level binding check and the chain guard must
    both find their version row and matching checksum.
    """
    import hashlib
    from app.models.stock_ml_registry import MLRegistryBase
    from app.models.stock_research_dataset import DatasetBase, DatasetSchemaVersion
    from app.models.stock_research_v2_job import V2JobBase, V2JobSchemaVersion
    from app.services.stock_research_v2.archive import SCHEMA_PATH, SCHEMA_VERSION
    from app.services.stock_research_v2.jobs import SCHEMA_PATH as CHAIN_PATH, SCHEMA_VERSION as CHAIN_VERSION
    async with factory() as session:
        DatasetBase.metadata.create_all(session.get_bind())
        V2JobBase.metadata.create_all(session.get_bind())
        # Migration 021 adds result foreign keys into 019, so the throwaway engine
        # carries the same prerequisite chain the real runner enforces.
        MLRegistryBase.metadata.create_all(session.get_bind())
        for model, version, path in ((DatasetSchemaVersion, SCHEMA_VERSION, SCHEMA_PATH), (V2JobSchemaVersion, CHAIN_VERSION, CHAIN_PATH)):
            known = await session.scalar(select(model.version).where(model.version == version))
            if known is None:
                session.add(model(version=version, checksum=hashlib.sha256(path.read_bytes()).hexdigest()))
        await session.commit()


async def registered_dataset(factory, owner, version, qualification="observation_only"):
    """A ready, purpose=dataset artifact plus its dataset row, owned by ``owner``."""
    from app.models.stock_research_dataset import StockResearchArtifact, StockResearchDataset
    await ensure_dataset_schema(factory)
    async with factory() as session:
        artifact_id, digest = uuid4(), "d" * 64
        session.add(StockResearchArtifact(id=artifact_id, owner_id=owner, trading_mode="virtual", idempotency_key=uuid4(),
            request_hash="e" * 64, request_id="a" * 32, sha256=digest, byte_size=128, relative_path=digest + ".json",
            purpose="dataset", state="ready"))
        dataset_id = uuid4()
        session.add(StockResearchDataset(id=dataset_id, manifest_artifact_id=artifact_id, data_version=version, qualification=qualification))
        await session.commit()
    return dataset_id, version, qualification


@pytest.mark.asyncio
async def test_every_research_route_exists_and_stays_closed_until_the_executor_switch_is_on(tmp_path):
    async with job_database() as (factory, owner, other):
        dataset = await registered_dataset(factory, owner, "a" * 64)
        app, context = application(factory, owner)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path, kind in ROUTES:
                body = command(kind, dataset).model_dump(mode="json")
                closed = await client.post(BASE + path, json=body)
                assert closed.status_code == 503, path
                assert closed.json()["detail"]["code"] == "v2_jobs_disabled", path
                assert closed.json()["detail"]["retryable"] is False, path
            assert (await client.get(BASE + "/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_a_research_route_refuses_every_other_kinds_body_instead_of_reinterpreting_it(tmp_path):
    async with job_database() as (factory, owner, other):
        dataset = await registered_dataset(factory, owner, "a" * 64)
        app, context = application(factory, owner)
        app.state.stock_v2_jobs_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path, kind in ROUTES:
                for foreign in ("research_ic", "backtest", "benchmark", "ml_train", "report", "dataset"):
                    if foreign == kind:
                        continue
                    body = command(foreign, dataset).model_dump(mode="json")
                    rejected = await client.post(BASE + path, json=body)
                    assert rejected.status_code == 422, (path, foreign)
                    assert rejected.json()["detail"]["code"] == "invalid_input", (path, foreign)
            # The observation route keeps refusing research bodies too.
            for foreign in ("research_ic", "backtest", "benchmark", "ml_train", "report"):
                body = command(foreign, dataset).model_dump(mode="json")
                assert (await client.post(BASE + "/datasets/jobs", json=body)).status_code == 422
            assert (await client.get(BASE + "/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_enabled_research_routes_accept_each_kind_idempotently_and_stay_scoped(tmp_path):
    from app.services.stock_research_v2.jobs import acquire_worker
    async with job_database() as (factory, owner, other):
        dataset = await registered_dataset(factory, owner, "a" * 64)
        async with factory() as session:
            await acquire_worker(session, uuid4())
            await session.commit()
        app, context = application(factory, owner)
        app.state.stock_v2_jobs_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path, kind in ROUTES:
                body = command(kind, dataset).model_dump(mode="json")
                accepted = await client.post(BASE + path, json=body)
                assert accepted.status_code == 202, (path, accepted.text)
                receipt = accepted.json()
                assert receipt["kind"] == kind, path
                assert receipt["status"] == "pending" and receipt["result"] is None, path
                assert receipt["method_version"] == "stock-research-task-v1", path
                assert receipt["workflow_version"] == "stock-v2-research-task-v1", path
                assert accepted.headers["X-Request-ID"] == receipt["request_id"], path
                assert accepted.headers["Cache-Control"] == "no-store", path
                # Re-sending the same idempotency key returns the same job, never a second one.
                again = await client.post(BASE + path, json=body)
                assert again.status_code == 202 and again.json()["id"] == receipt["id"], path
                # The same key with different parameters is a conflict, not a silent overwrite.
                if kind == "research_ic":
                    clash = command("research_ic", dataset, key=body["idempotency_key"], horizon=5)
                    assert (await client.post(BASE + path, json=clash.model_dump(mode="json"))).status_code == 409
                # Cancelling frees the per-scope active slot, so the next kind can be queued
                # without the backpressure limit masking a routing failure.
                assert (await client.post(BASE + "/jobs/" + receipt["id"] + "/cancel")).status_code == 200
            listed = (await client.get(BASE + "/jobs")).json()
            assert listed["total"] == len(ROUTES)
            assert {item["kind"] for item in listed["items"]} == {kind for _, kind in ROUTES}
            assert listed["items"] and all(item["status"] == "cancelled" for item in listed["items"])
            assert all("parameters" not in item for item in listed["items"])
            # The detail view returns each kind's own parameters, not the observation build.
            detail = (await client.get(BASE + "/jobs/" + listed["items"][0]["id"])).json()
            assert detail["parameters"]["kind"] == listed["items"][0]["kind"]
            for identity, mode in ((other, "virtual"), (owner, "live")):
                context.user.id, context.mode = identity, mode
                assert (await client.get(BASE + "/jobs")).json()["items"] == []
            context.user.id, context.mode = owner, "virtual"
            # Closing the switch must not break reads or cancels of already-created jobs.
            app.state.stock_v2_jobs_enabled = False
            first = listed["items"][0]["id"]
            assert (await client.post(BASE + "/jobs/" + first + "/cancel")).status_code == 200
            assert (await client.get(BASE + "/jobs/" + first)).status_code == 200
            assert (await client.post(BASE + "/ic/jobs", json=command("research_ic", dataset).model_dump(mode="json"))).status_code == 503


@pytest.mark.asyncio
async def test_research_submission_needs_the_fixed_dataset_and_refuses_a_foreign_or_stale_reference(tmp_path):
    from app.services.stock_research_v2.jobs import acquire_worker
    async with job_database() as (factory, owner, other):
        mine = await registered_dataset(factory, owner, "a" * 64)
        theirs = await registered_dataset(factory, other, "b" * 64)
        async with factory() as session:
            await acquire_worker(session, uuid4())
            await session.commit()
        app, context = application(factory, owner)
        app.state.stock_v2_jobs_enabled = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path, kind in ROUTES:
                foreign = await client.post(BASE + path, json=command(kind, theirs).model_dump(mode="json"))
                assert foreign.status_code == 404, path
                stale = await client.post(BASE + path, json=command(kind, (mine[0], "f" * 64, mine[2])).model_dump(mode="json"))
                assert stale.status_code == 409, path
                ungraded = await client.post(BASE + path, json=command(kind, (mine[0], mine[1], "backtest_ready")).model_dump(mode="json"))
                assert ungraded.status_code == 409, path
            assert (await client.get(BASE + "/jobs")).json()["total"] == 0
