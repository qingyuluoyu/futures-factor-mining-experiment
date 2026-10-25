from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from app.models.gp_job import GpJobBase
from app.models.gp_checkpoint import GpCheckpointSession
from app.models.user import User
from app.schemas.gp_jobs import CreateGpJob
from app.services.factor_mining.checkpoint_store import CheckpointStore
from app.services.factor_mining.jobs import GpJobError, GpJobService, claim_generation, finish_generation, heartbeat_generation, recover_leases, release_stopped
from app.services.factor_lab.search import search_stepwise
from tests.test_gp_checkpoint_store import storage, frozen_input
from tests.test_gp_checkpoint import config


async def prepared(factory, owner):
    async with factory() as session:
        GpJobBase.metadata.create_all(session.get_bind())
        User.__table__.create(session.get_bind(), checkfirst=True)
        session.add(User(id=owner, username="gp_fixture", hashed_password="not-a-login", status="active"))
        payload, cfg = frozen_input(), config(generations=3)
        saved = await CheckpointStore(session, owner, "futures", "virtual").create(uuid4(), payload, cfg)
        await session.commit()
        return CreateGpJob(input_id=saved["id"], idempotency_key=uuid4()), payload, cfg


@pytest.mark.asyncio
async def test_job_scope_idempotency_and_explicit_transitions():
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            service = GpJobService(session, owner, "virtual")
            created = await service.create(body, accepting_new=True)
            identifier = UUID(created["id"])
            await session.commit()
            assert (await service.create(body, accepting_new=False))["id"] == created["id"]
            with pytest.raises(GpJobError) as conflict:
                await service.create(body.model_copy(update={"title": "changed"}), accepting_new=False)
            assert conflict.value.code == "idempotency_conflict"
            for identity, mode in ((uuid4(), "virtual"), (owner, "live")):
                with pytest.raises(GpJobError) as denied:
                    await GpJobService(session, identity, mode).get(identifier)
                assert denied.value.status == 404
            paused = await service.pause(identifier)
            assert paused["status"] == "paused"
            assert (await service.resume(identifier, accepting_new=True))["status"] == "pending"
            assert (await service.cancel(identifier))["status"] == "cancelled"
            with pytest.raises(GpJobError):
                await service.resume(identifier, accepting_new=True)


@pytest.mark.asyncio
async def test_running_pause_keeps_slot_until_process_stopped_and_late_result_cannot_write():
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            service = GpJobService(session, owner, "virtual")
            created = await service.create(body, accepting_new=True)
            identifier = UUID(created["id"])
            await session.commit()
            claimed = await claim_generation(session)
            token = claimed.lease_token
            await session.commit()
            assert await heartbeat_generation(session, identifier, token)
            await service.pause(identifier)
            await session.commit()
            assert not await heartbeat_generation(session, identifier, token)
            assert await claim_generation(session) is None
            with pytest.raises(GpJobError) as held:
                await service.resume(identifier, accepting_new=True)
            assert held.value.code == "compute_stop_pending"
            snapshot = next(search_stepwise(payload["bars"], "1d", cfg))
            assert not await finish_generation(session, identifier, token, snapshot.checkpoint, [])
            assert await release_stopped(session, identifier, token)
            await session.commit()
            assert (await service.resume(identifier, accepting_new=True))["status"] == "pending"


@pytest.mark.asyncio
async def test_generations_and_final_result_are_committed_with_input_checkpoint():
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        snapshots = list(search_stepwise(payload["bars"], "1d", cfg))
        async with factory() as session:
            service = GpJobService(session, owner, "virtual")
            created = await service.create(body, accepting_new=True)
            identifier = UUID(created["id"])
            await session.commit()
            for snapshot in snapshots:
                claimed = await claim_generation(session)
                token = claimed.lease_token
                await session.commit()
                champions = [{"tokens": item.tokens, "text": item.text, "metrics": item.metrics, "composite": item.composite} for item in snapshot.champions]
                assert await finish_generation(session, identifier, token, snapshot.checkpoint, champions)
                await session.commit()
            result = await service.result(identifier)
            assert result["generation"] == 3 and result["validation_status"] == "research_only"
            assert result["source_certified"] is False
            assert (await service.get(identifier))["status"] == "completed"


@pytest.mark.asyncio
async def test_expired_lease_pauses_but_does_not_release_or_retry_before_child_deadline(monkeypatch):
    import app.services.factor_mining.jobs as module
    owner = uuid4()
    clock = datetime.now(timezone.utc)
    monkeypatch.setattr(module, "now", lambda: clock)
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            service = GpJobService(session, owner, "virtual")
            created = await service.create(body, accepting_new=True)
            identifier = UUID(created["id"])
            await session.commit()
            claimed = await claim_generation(session)
            deadline, token = claimed.generation_deadline, claimed.lease_token
            await session.commit()
            clock += timedelta(seconds=20)
            await recover_leases(session)
            assert (await service.get(identifier))["status"] == "paused"
            assert not await release_stopped(session, identifier, uuid4())
            with pytest.raises(GpJobError):
                await service.resume(identifier, accepting_new=True)
            clock = deadline + timedelta(seconds=11)
            await recover_leases(session)
            assert (await service.resume(identifier, accepting_new=True))["status"] == "pending"


def test_new_job_schema_does_not_accept_model_scope_or_bars():
    for extra in [{"trading_mode": "live"}, {"confirmed": True}, {"bars": []}, {"user_id": str(uuid4())}]:
        with pytest.raises(ValidationError):
            CreateGpJob.model_validate({"input_id": str(uuid4()), "idempotency_key": str(uuid4()), **extra})


@pytest.mark.asyncio
async def test_freezing_owner_mid_generation_prevents_heartbeat_and_result_write():
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            service = GpJobService(session, owner, "virtual")
            created = await service.create(body, accepting_new=True)
            identifier = UUID(created["id"])
            await session.commit()
            claimed = await claim_generation(session)
            token = claimed.lease_token
            await session.commit()
            user = await session.scalar(select(User).where(User.id == owner))
            user.status = "frozen"
            await session.commit()
            assert not await heartbeat_generation(session, identifier, token)
            snapshot = next(search_stepwise(payload["bars"], "1d", cfg))
            assert not await finish_generation(session, identifier, token, snapshot.checkpoint, [])
            assert (await CheckpointStore(session, owner, "futures", "virtual").load(body.input_id)).generation == 0


@pytest.mark.asyncio
async def test_malformed_champion_cannot_be_committed_and_deterministic_process_failure_is_terminal():
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            service = GpJobService(session, owner, "virtual")
            created = await service.create(body, accepting_new=True)
            identifier = UUID(created["id"])
            await session.commit()
            claimed = await claim_generation(session)
            token = claimed.lease_token
            await session.commit()
            snapshot = next(search_stepwise(payload["bars"], "1d", cfg))
            with pytest.raises(GpJobError):
                await finish_generation(session, identifier, token, snapshot.checkpoint, [{"tokens": [999999], "text": "not-real", "composite": 0, "metrics": {}}])
            assert (await CheckpointStore(session, owner, "futures", "virtual").load(body.input_id)).generation == 0
            await release_stopped(session, identifier, token, error_code="gp_process_invalid")
            assert (await service.get(identifier))["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["text", "metric", "missing_metric", "unknown_diagnostic", "trials", "empty"])
async def test_champion_formula_and_base_metrics_must_match_checkpoint(tamper):
    from copy import deepcopy
    from dataclasses import asdict
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            created = await GpJobService(session, owner, "virtual").create(body, accepting_new=True)
            await session.commit()
            claimed = await claim_generation(session)
            token = claimed.lease_token
            await session.commit()
            snapshot = next(search_stepwise(payload["bars"], "1d", cfg))
            champions = deepcopy([asdict(item) for item in snapshot.champions])
            assert champions
            if tamper == "text":
                champions[0]["text"] = "AN ENTIRELY DIFFERENT FORMULA"
            elif tamper == "metric":
                champions[0]["metrics"]["ann_ret"] = 999999999.0
            elif tamper == "missing_metric":
                champions[0]["metrics"].pop("ann_ret")
            elif tamper == "unknown_diagnostic":
                champions[0]["metrics"]["source_certified"] = True
            elif tamper == "trials":
                champions[0]["metrics"]["trials"] = 5000
            else:
                champions = []
            with pytest.raises(GpJobError, match="invalid_process_result"):
                await finish_generation(session, UUID(created["id"]), token, snapshot.checkpoint, champions)
            assert (await CheckpointStore(session, owner, "futures", "virtual").load(body.input_id)).generation == 0


@pytest.mark.asyncio
async def test_worker_corrupt_frozen_input_is_terminal_not_resumable():
    from app.services.factor_mining.process_worker import GpCheckpointWorker
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            created = await GpJobService(session, owner, "virtual").create(body, accepting_new=True)
            await session.execute(update(GpCheckpointSession).where(GpCheckpointSession.id == body.input_id).values(input_hash="0" * 64))
            await session.commit()
        assert await GpCheckpointWorker(factory).run_once()
        async with factory() as session:
            result = await GpJobService(session, owner, "virtual").get(UUID(created["id"]))
            assert result["status"] == "failed"
            assert result["error_code"] == "stored_input_corrupt"
            assert result["compute_stop_pending"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [{"total_generations": 0}, {"generation": 4}, {"runtime_seconds": 1801.0}])
async def test_job_database_enforces_progress_and_finite_runtime_bounds(values):
    from sqlalchemy.exc import IntegrityError
    from app.models.gp_job import GpJob
    owner = uuid4()
    async with storage() as factory:
        body, payload, cfg = await prepared(factory, owner)
        async with factory() as session:
            created = await GpJobService(session, owner, "virtual").create(body, accepting_new=True)
            await session.commit()
            with pytest.raises(IntegrityError):
                await session.execute(update(GpJob).where(GpJob.id == UUID(created["id"])).values(**values))
                await session.flush()
            await session.rollback()
