import asyncio
import importlib.util
from pathlib import Path
import re
from uuid import UUID, uuid4

from sqlalchemy import func, select, text


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api.factor_mining import router
from app.models.gp_checkpoint import GpCheckpointSession
from app.services.factor_lab.search import search_stepwise
from app.services.factor_mining.checkpoint_store import CheckpointError, CheckpointStore
from scripts.migrate_gp_checkpoint import VERSION, migrate_gp_checkpoint
from tests.test_gp_checkpoint import config
from tests.test_gp_checkpoint_store import frozen_input


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    application.include_router(router)
    async with scoped() as session:
        namespace = await session.scalar(text("SELECT current_schema()"))
        if not re.fullmatch(r"qa_completion_b02_[a-f0-9]{32}", namespace):
            raise RuntimeError("Unsafe schema")
        public_tasks = await session.scalar(text("SELECT count(*) FROM public.factor_mining_tasks"))
        public_tables_before = await session.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'gp_checkpoint_%'"))
        try:
            await migrate_gp_checkpoint(await session.connection(), check=True)
            raise AssertionError("Expected missing migration")
        except RuntimeError:
            check("b10_check_only_does_not_create_tables", await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.gp_checkpoint_sessions')")) is None)
        await migrate_gp_checkpoint(await session.connection())
        await session.rollback()
        check("b10_migration_transaction_rollback_removes_uncommitted_schema", await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.gp_checkpoint_sessions')")) is None)
        check("b10_expand_migration_applied", (await migrate_gp_checkpoint(await session.connection()))["applied"])
        await session.commit()
    async with scoped() as session:
        check("b10_repeat_migration_and_check", not (await migrate_gp_checkpoint(await session.connection()))["applied"] and (await migrate_gp_checkpoint(await session.connection(), check=True))["checked"])
        await session.commit()
    payload, cfg, key = frozen_input(), config(generations=3), uuid4()
    payload["bars"][0]["volume"] = 123.45

    async def create_once():
        async with scoped() as session:
            result = await CheckpointStore(session, owner_id, "futures", "virtual").create(key, payload, cfg)
            await session.commit()
            return result

    created = await asyncio.gather(create_once(), create_once())
    identifier = UUID(created[0]["id"])
    check("b10_concurrent_same_scope_key_creates_one_fixed_input", created[0]["id"] == created[1]["id"])
    async with scoped() as session:
        check("b10_only_one_private_row", await session.scalar(select(func.count()).select_from(GpCheckpointSession)) == 1)
        for identity, mode in ((other_id, "virtual"), (owner_id, "live")):
            try:
                await CheckpointStore(session, identity, "futures", mode).load(identifier)
                raise AssertionError("Expected owner/mode denial")
            except CheckpointError as error:
                check("b10_private_inputs_do_not_cross_owner_or_jwt_mode", error.status == 404)
        try:
            await CheckpointStore(session, owner_id, "stock", "virtual").load(identifier)
            raise AssertionError("Expected market denial")
        except CheckpointError as error:
            check("b10_stock_cannot_reuse_futures_snapshot", error.status == 403)
        try:
            await CheckpointStore(session, owner_id, "futures", "virtual").create(key, {**payload, "symbol": "rb2702"}, cfg)
            raise AssertionError("Expected idempotency conflict")
        except CheckpointError as error:
            check("b10_key_bound_to_full_source_and_configuration", error.code == "idempotency_conflict")
    snapshots = list(search_stepwise(payload["bars"], "1d", cfg))
    check("b10_real_kernel_produces_three_checkpoints", len(snapshots) == 3)

    async def commit_once():
        async with scoped() as session:
            try:
                result = await CheckpointStore(session, owner_id, "futures", "virtual").commit_generation(identifier, 0, snapshots[0].checkpoint)
                await session.commit()
                return result["revision"]
            except CheckpointError as error:
                if error.code == "stored_checkpoint_corrupt":
                    row = await session.scalar(select(GpCheckpointSession).where(GpCheckpointSession.id == identifier))
                    def differences(expected, actual, path="state"):
                        if isinstance(expected, dict) and isinstance(actual, dict):
                            return [item for name in expected for item in differences(expected[name], actual[name], path + "." + name)]
                        if isinstance(expected, list) and isinstance(actual, list):
                            return [item for index, values in enumerate(zip(expected, actual)) for item in differences(*values, path + "." + str(index))]
                        return [path + ":" + repr(expected) + "->" + repr(actual)] if repr(expected) != repr(actual) else []
                    print("b10_state_roundtrip_differences", differences(snapshots[0].checkpoint, row.checkpoint_json)[:10])
                await session.rollback()
                return error.code

    revisions = await asyncio.gather(commit_once(), commit_once())
    print("b10_CAS_outcomes", revisions)
    check("b10_concurrent_generation_CAS_one_commit_one_stale", set(revisions) == {1, "stale_revision"})
    try:
        async with scoped() as session:
            await CheckpointStore(session, owner_id, "futures", "virtual").commit_generation(identifier, 1, snapshots[1].checkpoint)
            raise RuntimeError("intentional interrupted transaction")
    except RuntimeError:
        pass
    async with scoped() as session:
        store = CheckpointStore(session, owner_id, "futures", "virtual")
        saved = await store.load(identifier)
        check("b10_interrupted_generation_keeps_previous_state", saved.generation == 1 and saved.checkpoint_hash == snapshots[0].checkpoint["state_hash"])
        check("b10_fractional_volume_not_truncated_or_zero_filled", saved.input_json["bars"][0]["volume"] == 123.45)
        resumed = list(search_stepwise(saved.input_json["bars"], "1d", cfg, checkpoint=saved.checkpoint_json))
        check("b10_restart_from_actual_PG_JSON_is_exact", resumed == snapshots[1:])
        for snapshot in resumed:
            await store.commit_generation(identifier, snapshot.generation - 1, snapshot.checkpoint)
        await session.commit()
    async with scoped() as session:
        check("b10_final_checkpoint_generation_three", (await CheckpointStore(session, owner_id, "futures", "virtual").load(identifier)).generation == 3)
        await session.execute(text("ALTER TABLE gp_checkpoint_sessions ADD COLUMN qa_schema_drift INTEGER"))
        try:
            await migrate_gp_checkpoint(await session.connection(), check=True)
            raise AssertionError("Expected structure drift")
        except RuntimeError:
            check("b10_changed_table_structure_rejected", True)
        await session.rollback()
    async with scoped() as session:
        await session.execute(text("UPDATE gp_checkpoint_schema_versions SET checksum=:bad WHERE version=:version"), {"bad": "0" * 64, "version": VERSION})
        try:
            await migrate_gp_checkpoint(await session.connection(), check=True)
            raise AssertionError("Expected checksum drift")
        except RuntimeError:
            check("b10_changed_migration_rejected", True)
        await session.rollback()
    supported = await client.get("/api/factor-mining/supported", headers=headers(owner_id))
    check("b10_HTTP_does_not_claim_worker_is_ready", supported.status_code == 200 and supported.json()["accepting"] is False and supported.json()["exact_resume_available"] is False)
    check("b10_anonymous_capability_still_401", (await client.get("/api/factor-mining/supported")).status_code == 401)
    check("b10_stock_capability_409", (await client.get("/api/factor-mining/supported", headers=headers(stock_id))).status_code == 409)
    for mode in ("live", "virtual"):
        response = await client.post("/api/factor-mining/tasks", json={"symbol": "rb2701"}, headers=headers(owner_id, mode))
        check("b10_unconnected_worker_no_fake_task_no_source_download", response.status_code == 503 and response.headers.get("X-Research-Error") == "checkpoint_worker_not_connected" and response.headers.get("Cache-Control") == "no-store")
    async with scoped() as session:
        check("b10_old_public_tasks_and_schema_not_modified", await session.scalar(text("SELECT count(*) FROM public.factor_mining_tasks")) == public_tasks and await session.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'gp_checkpoint_%'")) == public_tables_before)


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b10"))
