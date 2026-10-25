import asyncio
from datetime import timedelta
import importlib.util
from pathlib import Path
import re
from uuid import UUID, uuid4
from dataclasses import asdict
from copy import deepcopy

from sqlalchemy import func, select, text, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.gp_jobs import router
from app.core.deps import get_settings
from app.models.gp_job import GpJob
from app.models.gp_checkpoint import GpCheckpointSession
from app.schemas.gp_jobs import CreateGpJob
from app.services.factor_lab.search import search_stepwise
from app.services.factor_mining.checkpoint_store import CheckpointStore
from app.services.factor_mining.jobs import GpJobError, GpJobService, claim_generation, finish_generation, release_stopped
from app.services.factor_mining.startup import start_gp_worker
from scripts.migrate_gp_checkpoint import migrate_gp_checkpoint
from scripts.migrate_gp_jobs import VERSION, migrate_gp_jobs
from tests.test_gp_checkpoint import config
from tests.test_gp_checkpoint_store import frozen_input


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    application.include_router(router)
    configured = settings.model_copy(update={"gp_jobs_enabled": True})
    application.dependency_overrides[get_settings] = lambda: configured
    base = "/api/factor-mining/v2"
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Unsafe schema")
        public_count = await session.scalar(text("SELECT count(*) FROM public.factor_mining_tasks"))
        public_gp_tables = await session.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'gp_%'"))
        await migrate_gp_checkpoint(await session.connection())
        await session.commit()
    unavailable = await client.get(base + "/availability", headers=headers(owner_id))
    check("b11_missing_009_not_advertised_as_enabled", unavailable.status_code == 200 and unavailable.json()["enabled"] is False)
    async with scoped() as session:
        try:
            await migrate_gp_jobs(await session.connection(), check=True)
            raise AssertionError("Expected missing009")
        except RuntimeError:
            check("b11_check_only_does_not_create_jobs", await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.gp_checkpoint_jobs')")) is None)
        try:
            await migrate_gp_jobs(await session.connection())
        except RuntimeError as error:
            print("B11 isolated migration validation:", str(error))
            raise
        await session.rollback()
        check("b11_migration_transaction_rollback_is_atomic", await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.gp_checkpoint_jobs')")) is None)
        check("b11_009_expand_migration", (await migrate_gp_jobs(await session.connection()))["applied"])
        await session.commit()
    async with scoped() as session:
        check("b11_repeat_and_check_schema_types_fk_indexes", not (await migrate_gp_jobs(await session.connection()))["applied"] and (await migrate_gp_jobs(await session.connection(), check=True))["checked"])
        await session.commit()
    await start_gp_worker(application, scoped, configured)
    worker = application.state.gp_checkpoint_worker
    check("b11_real_worker_started_only_after_matching_schema", worker is not None)

    async def eventually(callback, *, timeout=25):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            result = await callback()
            if result:
                return result
            await asyncio.sleep(.04)
        raise AssertionError("Timed out waiting for bounded private GP worker")

    async def healthy():
        return worker.healthy

    async def status(identifier, wanted):
        response = await client.get(f"{base}/tasks/{identifier}", headers=headers(owner_id))
        if response.status_code != 200:
            raise AssertionError("Unexpected task query status")
        result = response.json()
        if result["status"] in {"failed", "expired"}:
            raise AssertionError("GP task failed: " + str(result["error_code"]))
        return result if result["status"] in wanted else None

    async def freeze(cfg):
        payload = frozen_input()
        async with scoped() as session:
            saved = await CheckpointStore(session, owner_id, "futures", "virtual").create(uuid4(), payload, cfg)
            await session.commit()
        return payload, UUID(saved["id"])

    try:
        await eventually(healthy)
        available = await client.get(base + "/availability", headers=headers(owner_id))
        check("b11_real_healthy_worker_and_no_fake_ingestion_capability", available.json()["enabled"] and available.json()["accepting"] and available.json()["source_ingestion_available"] is False)
        cfg = config(generations=3)
        payload, input_id = await freeze(cfg)
        body = {"input_id": str(input_id), "idempotency_key": str(uuid4())}
        responses = await asyncio.gather(*(client.post(base + "/tasks", json=body, headers=headers(owner_id)) for _ in range(2)))
        check("b11_concurrent_real_JWT_HTTP_submission_deduplicated", all(item.status_code == 202 for item in responses) and responses[0].json()["id"] == responses[1].json()["id"])
        identifier = responses[0].json()["id"]
        check("b11_creation_contract_trace_location_and_scope", responses[0].headers.get("Location") == f"{base}/tasks/{identifier}" and bool(responses[0].headers.get("X-Request-ID")) and responses[0].json()["scope"] == {"market": "futures", "mode": "virtual"})
        for identity, mode in ((other_id, "virtual"), (owner_id, "live")):
            check("b11_foreign_owner_and_JWT_mode_404", (await client.get(f"{base}/tasks/{identifier}", headers=headers(identity, mode))).status_code == 404)
            check("b11_foreign_cancel_404", (await client.post(f"{base}/tasks/{identifier}/cancel", headers=headers(identity, mode))).status_code == 404)
        check("b11_stock_market_409", (await client.get(base + "/tasks", headers=headers(stock_id))).status_code == 409)
        check("b11_anonymous_401", (await client.get(base + "/tasks")).status_code == 401)
        check("b11_pagination_bounds_422", (await client.get(base + "/tasks?limit=51", headers=headers(owner_id))).status_code == 422)
        completed = await eventually(lambda: status(identifier, {"completed"}))
        check("b11_real_killable_child_completes_three_persisted_generations", completed["generation"] == 3 and completed["attempts"] == 3)
        saved = await client.get(f"{base}/tasks/{identifier}/result", headers=headers(owner_id))
        expected = list(search_stepwise(payload["bars"], "1d", cfg))[-1]
        expected_champions = [{"tokens": item.tokens, "text": item.text, "metrics": item.metrics, "composite": item.composite} for item in expected.champions]
        check("b11_real_subprocess_results_equal_uninterrupted_kernel", saved.status_code == 200 and saved.json()["champions"] == expected_champions and saved.json()["source_certified"] is False)
        async with scoped() as session:
            frozen = await CheckpointStore(session, owner_id, "futures", "virtual").load(input_id)
            check("b11_input_checkpoint_and_report_committed_together", frozen.generation == 3 and frozen.checkpoint_hash == saved.json()["checkpoint_hash"])
        long_cfg = config(population=120, generations=30)
        long_payload, long_input = await freeze(long_cfg)
        second_body = {"input_id": str(long_input), "idempotency_key": str(uuid4())}
        second = await client.post(base + "/tasks", json=second_body, headers=headers(owner_id))
        check("b11_second_task_accepted_without_usage_count_quota", second.status_code == 202)
        second_id = second.json()["id"]
        await eventually(lambda: status(second_id, {"running"}))
        paused = await client.post(f"{base}/tasks/{second_id}/pause", headers=headers(owner_id))
        check("b11_running_task_pause_is_real_state_transition", paused.status_code == 200 and paused.json()["status"] == "paused")
        paused_generation = paused.json()["generation"]
        async def stopped():
            response = await client.get(f"{base}/tasks/{second_id}", headers=headers(owner_id))
            return response.json() if not response.json()["compute_stop_pending"] else None
        stopped_view = await eventually(stopped)
        check("b11_late_child_cannot_overwrite_paused_generation", stopped_view["status"] == "paused" and stopped_view["generation"] == paused_generation)
        await worker.stop()
        check("b11_worker_stop_reaps_active_process_before_shutdown_returns", not worker.healthy)
        replay = await client.post(base + "/tasks", json=second_body, headers=headers(owner_id))
        check("b11_offline_same_key_reconciles_without_execution", replay.status_code == 202 and replay.json()["id"] == second_id)
        check("b11_offline_new_key_not_created", (await client.post(base + "/tasks", json={**second_body, "idempotency_key": str(uuid4())}, headers=headers(owner_id))).status_code == 503)
        check("b11_cancel_while_worker_offline", (await client.post(f"{base}/tasks/{second_id}/cancel", headers=headers(owner_id))).json()["status"] == "cancelled")
        async with scoped() as session:
            check("b11_two_jobs_only_no_silent_retries", await session.scalar(select(func.count()).select_from(GpJob)) == 2)
        forged_payload, forged_input = await freeze(cfg)
        async with scoped() as session:
            created = await GpJobService(session, owner_id, "virtual").create(CreateGpJob(input_id=forged_input, idempotency_key=uuid4()), accepting_new=True)
            await session.commit()
            claimed = await claim_generation(session)
            token, forged_id = claimed.lease_token, UUID(created["id"])
            await session.commit()
            snapshot = next(search_stepwise(forged_payload["bars"], "1d", cfg))
            champions = deepcopy([asdict(item) for item in snapshot.champions])
            champions[0]["metrics"]["ann_ret"] = 999999999.0
            try:
                await finish_generation(session, forged_id, token, snapshot.checkpoint, champions)
                raise AssertionError("Expected forged metric rejection")
            except GpJobError as error:
                check("b11_real_PG_forged_metrics_rejected", error.code == "invalid_process_result")
            await session.rollback()
            check("b11_forged_result_leaves_input_generation_unchanged", (await CheckpointStore(session, owner_id, "futures", "virtual").load(forged_input)).generation == 0)
            await release_stopped(session, forged_id, token, error_code="invalid_process_result")
            await session.commit()
        corrupt_payload, corrupt_input = await freeze(cfg)
        async with scoped() as session:
            corrupt = await GpJobService(session, owner_id, "virtual").create(CreateGpJob(input_id=corrupt_input, idempotency_key=uuid4()), accepting_new=True)
            await session.execute(update(GpCheckpointSession).where(GpCheckpointSession.id == corrupt_input).values(input_hash="0" * 64))
            await session.commit()
        await worker.run_once()
        failed = await client.get(f"{base}/tasks/{corrupt['id']}", headers=headers(owner_id))
        check("b11_real_PG_corrupt_input_is_terminal_and_slot_released", failed.status_code == 200 and failed.json()["status"] == "failed" and failed.json()["error_code"] == "stored_input_corrupt" and not failed.json()["compute_stop_pending"])
        async with scoped() as session:
            await session.execute(text("ALTER TABLE gp_checkpoint_jobs ADD COLUMN qa_drift INTEGER"))
            try:
                await migrate_gp_jobs(await session.connection(), check=True)
                raise AssertionError("Expected schema drift")
            except RuntimeError:
                check("b11_schema_drift_rejected", True)
            await session.rollback()
        async with scoped() as session:
            await session.execute(text("UPDATE gp_job_schema_versions SET checksum=:bad WHERE version=:version"), {"bad": "0" * 64, "version": VERSION})
            try:
                await migrate_gp_jobs(await session.connection(), check=True)
                raise AssertionError("Expected checksum drift")
            except RuntimeError:
                check("b11_checksum_drift_rejected", True)
            await session.rollback()
            check("b11_no_public_GP_schema_or_legacy_task_writes", await session.scalar(text("SELECT count(*) FROM public.factor_mining_tasks")) == public_count and await session.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'gp_%'")) == public_gp_tables)
    finally:
        if worker is not None:
            await worker.stop()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b11"))
