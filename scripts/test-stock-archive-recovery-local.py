import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import importlib.util
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from fastapi import HTTPException
from jose import jwt
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.auth import create_access_token, create_refresh_token
from app.core.deps import get_auth_context
from app.models.stock_archive_job import StockArchiveJob
from app.models.stock_archive_recovery import StockArchiveRecovery
from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.models.stock_eod import StockEodBatch
from app.models.user import User
from app.schemas.stock_archive_recovery import RecoveryRequest
from app.services import stock_archive_recovery as recovery
from app.services.stock_archive_worker import new_job_values
from scripts.migrate_stock_archive_jobs import migrate_stock_archive_jobs
from scripts.migrate_stock_archive_recovery import migrate_stock_archive_recovery
from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
from scripts.migrate_stock_eod import migrate_stock_eod
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings
from tests.test_stock_archive_recovery import NOW
from tests.test_stock_daily_archive import rehash, source_batch


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only dedicated local PG5433 is permitted")
    namespace = "qa_archive_recovery_" + uuid4().hex
    if not re.fullmatch(r"qa_archive_recovery_[a-f0-9]{32}", namespace):
        raise RuntimeError("Invalid owned namespace")
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    output = ROOT / "logs" / f"stock-archive-recovery-pg-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{namespace[-8:]}.json"
    checks = []
    report = {"checks": checks, "namespace": namespace, "source_calls": 0, "model_calls": 0, "cleanup": False}
    original_archive = recovery.archive_eod
    admin, other_admin, ordinary, frozen = (uuid4() for _ in range(4))

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    class ScopedEngine:
        @asynccontextmanager
        async def begin(self):
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{namespace}"'))
                yield connection

    @asynccontextmanager
    async def scoped():
        async with engine.connect() as connection:
            await connection.execute(text(f'SET SESSION search_path TO "{namespace}"'))
            await connection.execute(text("SELECT set_config('application_name', :name, false)"), {"name": namespace})
            await connection.commit()
            try:
                async with factory(bind=connection) as session:
                    yield session
            finally:
                await connection.rollback()
                await connection.execute(text("RESET search_path"))
                await connection.execute(text("RESET application_name"))
                await connection.commit()

    async def fingerprint():
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            value = {}
            value["history"] = [dict(item) for item in (await connection.execute(text("SELECT market_type, count(*) AS rows, sum(id) AS ids, sum(volume::numeric) AS volume, sum(close::numeric) AS prices FROM public.kline_history GROUP BY market_type ORDER BY market_type"))).mappings()]
            for table, ordering in (("stock_eod_batches", "trade_date"), ("stock_instruments", "code"), ("stock_archive_jobs", "id")):
                value[table] = dict((await connection.execute(text(f"SELECT count(*) AS rows, md5(string_agg(row_to_json(item)::text, '' ORDER BY {ordering})) AS digest FROM public.{table} AS item"))).mappings().one())
            value["archive"] = dict((await connection.execute(text("SELECT count(*) AS rows, sum(volume) AS lots, sum(close) AS prices FROM public.stock_daily_observations"))).mappings().one())
            value["users"] = dict((await connection.execute(text("SELECT count(*) AS rows, md5(string_agg(id::text || role::text || status::text, '' ORDER BY id)) AS digest FROM public.users"))).mappings().one())
            return value

    async def source(day, *, status="failed", corrupt=False):
        value = source_batch()
        value.trade_date = NOW.date().replace(day=day)
        value.snapshot_json["trade_date"] = value.trade_date.isoformat()
        value.snapshot_json["quotes"][0]["trade_date"] = value.trade_date.isoformat()
        rehash(value)
        if corrupt:
            value.snapshot_json["quotes"][0]["close"] = 999
        job = new_job_values({"id": value.id, "trade_date": min(value.trade_date, (NOW - timedelta(days=1)).date()), "data_version": value.data_version}, now=NOW - timedelta(minutes=1))
        job["trade_date"] = value.trade_date
        if status != "running":
            job.update(status=status, step=status, finished_at=NOW, updated_at=NOW,
                error_code="archive_validation_or_storage_failed", error_message="private upstream failure")
        async with scoped() as session:
            session.add(StockEodBatch(id=value.id, trade_date=value.trade_date, status=value.status, step=value.status,
                request_id=uuid4().hex, attempts=1, started_at=value.finished_at, deadline_at=value.finished_at + timedelta(minutes=2),
                finished_at=value.finished_at, method_version=value.method_version, data_version=value.data_version, snapshot_json=value.snapshot_json))
            session.add(StockArchiveJob(**job))
            await session.commit()
        body = RecoveryRequest(source_job_id=job["id"], expected_data_version=value.data_version, idempotency_key=uuid4(), reason="隔离测试核查存储故障已恢复")
        return value, job, body

    async def count_recoveries(job_id):
        async with scoped() as session:
            return await session.scalar(select(func.count()).select_from(StockArchiveRecovery).where(StockArchiveRecovery.source_job_id == job_id))

    async def reject(body, code, *, operator=admin, apply=False):
        try:
            if apply:
                await recovery.run_recovery(scoped, body, operator_id=operator, now=NOW)
            else:
                async with scoped() as session:
                    await recovery.inspect_recovery(session, body, operator_id=operator, now=NOW)
        except recovery.RecoveryError as error:
            return error.code == code
        return False

    async def cancelled_run(factory_override, body):
        try:
            await recovery.run_recovery(factory_override, body, operator_id=admin, now=NOW)
        except asyncio.CancelledError:
            return True
        return False

    before = await fingerprint()
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            await connection.execute(text(f'CREATE TABLE "{namespace}".users (LIKE public.users INCLUDING ALL)'))
            await connection.execute(text(f"CREATE TYPE \"{namespace}\".user_role_enum AS ENUM ('user', 'admin')"))
            await connection.execute(text(f"CREATE TYPE \"{namespace}\".user_status_enum AS ENUM ('active', 'frozen')"))
            await connection.execute(text(f'ALTER TABLE "{namespace}".users ALTER COLUMN role TYPE "{namespace}".user_role_enum USING role::text::"{namespace}".user_role_enum'))
            await connection.execute(text(f'ALTER TABLE "{namespace}".users ALTER COLUMN status TYPE "{namespace}".user_status_enum USING status::text::"{namespace}".user_status_enum'))
        for migration in (migrate_stock_eod, migrate_stock_daily_archive, migrate_stock_archive_jobs, migrate_stock_archive_recovery):
            applied = await migration(ScopedEngine())
            checked = await migration(ScopedEngine(), check=True)
            check(f"{applied['version']}_apply_check_repeat", applied["applied"] and checked["checked"] and not checked["applied"])
        async with scoped() as session:
            for identifier, role, status in ((admin, "admin", "active"), (other_admin, "admin", "active"), (ordinary, "user", "active"), (frozen, "admin", "frozen")):
                session.add(User(id=identifier, username="qa_recovery_" + identifier.hex[:16], hashed_password="disabled_isolated_test_user",
                    role=role, status=status, created_at=NOW, updated_at=NOW))
            await session.commit()
        async with scoped() as session:
            context = await get_auth_context(token=create_access_token(settings, str(admin), "admin"), session=session, settings=settings)
            check("real_signed_access_token_resolves_isolated_active_admin", context.user.id == admin and context.user.role == "admin")
        for name, token, expected in (("refresh_token_not_access", create_refresh_token(settings, str(admin)), 401),
                ("frozen_admin_rejected", create_access_token(settings, str(frozen), "admin"), 403),
                ("signed_nonexistent_user_rejected", create_access_token(settings, str(uuid4()), "admin"), 401),
                ("expired_access_token_rejected", jwt.encode({"sub": str(admin), "type": "access", "exp": 1}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm), 401)):
            try:
                async with scoped() as session:
                    await get_auth_context(token=token, session=session, settings=settings)
            except HTTPException as error:
                check(name, error.status_code == expected)
            else:
                check(name, False)
        healthy, job, body = await source(1)
        for name, operator in (("ordinary_user", ordinary), ("frozen_admin", frozen), ("unknown_user", uuid4())):
            check(name + "_cannot_preflight_or_apply", await reject(body, "admin_required", operator=operator)
                and await reject(body, "admin_required", operator=operator, apply=True))
        async with scoped() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            result = await recovery.inspect_recovery(session, body, operator_id=admin, now=NOW)
            check("dry_run_is_real_read_only_and_no_attempt_consumed", result["status"] == "dry_run" and result["row_count"] == 1
                and await session.scalar(select(func.count()).select_from(StockArchiveRecovery)) == 0)
        results = await asyncio.wait_for(asyncio.gather(*(recovery.run_recovery(scoped, body, operator_id=admin, now=NOW) for _ in range(6))), 30)
        check("six_concurrent_same_keys_share_one_persistent_recovery", len({item["id"] for item in results}) == 1 and await count_recoveries(job["id"]) == 1)
        replay = await recovery.run_recovery(scoped, body, operator_id=admin, now=NOW)
        async with scoped() as session:
            original = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.id == job["id"]))
            stored = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.source_job_id == job["id"]))
            check("archive_and_recovery_completed_commit_together_original_failure_immutable", replay["status"] == stored.status == "completed"
                and original.status == "failed" and original.attempts == 1 and original.error_message == job["error_message"]
                and await session.scalar(select(func.count()).select_from(StockDailyObservation).where(StockDailyObservation.batch_id == stored.archive_batch_id)) == 1)
        check("same_key_changed_body_is_conflict", await reject(body.model_copy(update={"reason": "不同恢复理由不能复用同一个幂等键"}), "idempotency_conflict", apply=True))
        check("completed_source_new_key_is_not_another_attempt", await reject(body.model_copy(update={"idempotency_key": uuid4()}), "already_recovered", apply=True) and await count_recoveries(job["id"]) == 1)
        check("report_omits_private_reason_and_failure_text", "reason" not in replay and "private" not in str(replay) and replay["source_calls"] == replay["model_calls"] == 0)
        corrupt, corrupt_job, corrupt_body = await source(2, corrupt=True)
        check("tampered_snapshot_rejected_before_quota", await reject(corrupt_body, "source_validation_failed", apply=True) and await count_recoveries(corrupt_job["id"]) == 0)
        check("expected_version_mismatch_rejected_before_quota", await reject(corrupt_body.model_copy(update={"expected_data_version": "0" * 64}), "source_version_changed", apply=True))
        active, active_job, active_body = await source(3, status="running")
        check("original_running_not_recovered_even_by_admin", await reject(active_body, "source_job_not_recoverable", apply=True))
        atomic, atomic_job, atomic_body = await source(4)
        async def fail_after_write(session, value, *, now):
            await original_archive(session, value, now=now)
            await session.execute(text("SELECT 1/0"))
        recovery.archive_eod = fail_after_write
        try:
            failure = await recovery.run_recovery(scoped, atomic_body, operator_id=admin, now=NOW)
            repeated = await recovery.run_recovery(scoped, atomic_body, operator_id=admin, now=NOW)
            second_body = atomic_body.model_copy(update={"idempotency_key": uuid4()})
            second = await recovery.run_recovery(scoped, second_body, operator_id=other_admin, now=NOW)
        finally:
            recovery.archive_eod = original_archive
        async with scoped() as session:
            check("database_failure_rolls_back_archive_but_records_one_failed_attempt", failure["status"] == repeated["status"] == "failed" and failure["id"] == repeated["id"]
                and await session.scalar(select(func.count()).select_from(StockDailyArchiveBatch).where(StockDailyArchiveBatch.source_batch_id == atomic.id)) == 0)
        check("max_two_recoveries_across_admins_terminal_replay_does_not_charge", second["attempt_number"] == 2 and second["status"] == "failed"
            and await reject(atomic_body.model_copy(update={"idempotency_key": uuid4()}), "recovery_limit", apply=True) and await count_recoveries(atomic_job["id"]) == 2)
        held, held_job, held_body = await source(5, status="cancelled")
        entered = asyncio.Event()
        async def hold(session, value, *, now):
            await original_archive(session, value, now=now)
            entered.set()
            await asyncio.Event().wait()
        recovery.archive_eod = hold
        task = asyncio.create_task(recovery.run_recovery(scoped, held_body, operator_id=admin, now=NOW))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            check("new_key_cannot_overlap_running_recovery", await reject(held_body.model_copy(update={"idempotency_key": uuid4()}), "recovery_running", apply=True)
                and await count_recoveries(held_job["id"]) == 1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Cancellation must propagate")
        finally:
            recovery.archive_eod = original_archive
            if not task.done():
                task.cancel()
        async with scoped() as session:
            cancelled = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.source_job_id == held_job["id"]))
            check("cancel_rolls_back_archive_records_cancelled_and_preserves_original", cancelled.status == "cancelled"
                and await session.scalar(select(func.count()).select_from(StockDailyArchiveBatch).where(StockDailyArchiveBatch.source_batch_id == held.id)) == 0
                and await session.scalar(select(StockArchiveJob.status).where(StockArchiveJob.id == held_job["id"])) == "cancelled")
        result = await recovery.run_recovery(scoped, held_body.model_copy(update={"idempotency_key": uuid4()}), operator_id=other_admin, now=NOW)
        check("explicit_second_key_recovers_cancelled_source", result["status"] == "completed" and result["attempt_number"] == 2)
        uncommitted, uncommitted_job, uncommitted_body = await source(6)
        first = True
        class BeforeCommit:
            def __init__(self, session):
                self.session = session
            def __getattr__(self, name):
                return getattr(self.session, name)
            async def commit(self):
                raise asyncio.CancelledError()
        @asynccontextmanager
        async def cancel_before_commit():
            nonlocal first
            interrupt, first = first, False
            async with scoped() as session:
                yield BeforeCommit(session) if interrupt else session
        check("uncommitted_claim_cancel_propagates_no_fake_failure", await cancelled_run(cancel_before_commit, uncommitted_body)
            and await count_recoveries(uncommitted_job["id"]) == 0)
        committed, committed_job, committed_body = await source(7)
        exits = 0
        @asynccontextmanager
        async def cancel_on_first_exit():
            nonlocal exits
            exits += 1
            interrupt = exits == 1
            async with scoped() as session:
                yield session
            if interrupt:
                raise asyncio.CancelledError()
        check("committed_claim_exit_cancel_propagates", await cancelled_run(cancel_on_first_exit, committed_body))
        async with scoped() as session:
            check("committed_claim_cancellation_has_real_terminal_record", await session.scalar(select(StockArchiveRecovery.status).where(StockArchiveRecovery.source_job_id == committed_job["id"])) == "cancelled")
        done, done_job, done_body = await source(8)
        exits = 0
        @asynccontextmanager
        async def cancel_on_second_exit():
            nonlocal exits
            exits += 1
            interrupt = exits == 2
            async with scoped() as session:
                yield session
            if interrupt:
                raise asyncio.CancelledError()
        check("committed_completion_exit_cancel_propagates", await cancelled_run(cancel_on_second_exit, done_body))
        async with scoped() as session:
            check("late_cancel_never_overwrites_committed_completion", await session.scalar(select(StockArchiveRecovery.status).where(StockArchiveRecovery.source_job_id == done_job["id"])) == "completed")
        stale, stale_job, stale_body = await source(9)
        async with scoped() as session:
            values, claimed, rejection = await recovery._claim(session, stale_body, operator_id=admin, now=NOW - timedelta(minutes=5))
            await session.commit()
        same_key = await recovery.run_recovery(scoped, stale_body, operator_id=admin, now=NOW)
        check("expired_same_key_only_reports_no_automatic_execution", same_key["status"] == "expired" and await count_recoveries(stale_job["id"]) == 1)
        renewed = await recovery.run_recovery(scoped, stale_body.model_copy(update={"idempotency_key": uuid4()}), operator_id=admin, now=NOW)
        async with scoped() as session:
            old = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.id == values["id"]))
            check("explicit_new_key_reconciles_expired_old_record_with_bounded_second_attempt", old.status == "failed" and old.error_code == "recovery_expired"
                and renewed["status"] == "completed" and renewed["attempt_number"] == 2)
        late, late_job, late_body = await source(11)
        commit_entered, release_commit = asyncio.Event(), asyncio.Event()
        sessions = 0
        class DelayedCompletionCommit:
            def __init__(self, session):
                self.session = session
            def __getattr__(self, name):
                return getattr(self.session, name)
            async def commit(self):
                await self.session.flush()
                commit_entered.set()
                await release_commit.wait()
                await self.session.commit()
        @asynccontextmanager
        async def delayed_completion():
            nonlocal sessions
            sessions += 1
            delay = sessions == 2
            async with scoped() as session:
                yield DelayedCompletionCommit(session) if delay else session
        first_task = asyncio.create_task(recovery.run_recovery(delayed_completion, late_body, operator_id=admin, now=NOW))
        second_task = None
        try:
            await asyncio.wait_for(commit_entered.wait(), 10)
            second_task = asyncio.create_task(recovery.run_recovery(scoped, late_body.model_copy(update={"idempotency_key": uuid4()}), operator_id=other_admin, now=NOW + timedelta(seconds=91)))
            waiting = False
            for attempt in range(60):
                async with engine.connect() as connection:
                    waiting = await connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name=:name AND wait_event_type='Lock' AND query LIKE '%stock_archive_recoveries%')"), {"name": namespace})
                if waiting:
                    break
                await asyncio.sleep(0.01)
            check("expiry_race_really_waited_for_inflight_completion_lock", waiting)
            release_commit.set()
            first_result = await asyncio.wait_for(first_task, 10)
            try:
                await asyncio.wait_for(second_task, 10)
            except recovery.RecoveryError as error:
                rejected_completed = error.code == "already_recovered"
            else:
                rejected_completed = False
            async with scoped() as session:
                check("expired_candidate_rereads_completed_after_lock_without_second_attempt", rejected_completed and first_result["status"] == "completed"
                    and await session.scalar(select(StockArchiveRecovery.status).where(StockArchiveRecovery.source_job_id == late_job["id"])) == "completed"
                    and await session.scalar(select(func.count()).select_from(StockArchiveRecovery).where(StockArchiveRecovery.source_job_id == late_job["id"])) == 1)
        finally:
            release_commit.set()
            for task in (first_task, second_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (first_task, second_task) if task is not None), return_exceptions=True)
        exhausted, exhausted_job, exhausted_body = await source(12)
        async with scoped() as session:
            first_claim, claimed, rejection = await recovery._claim(session, exhausted_body, operator_id=admin, now=NOW - timedelta(minutes=5))
            await session.commit()
        await recovery._save_failure(scoped, first_claim, cancelled=False, now=NOW - timedelta(minutes=4))
        async with scoped() as session:
            last_claim, claimed, rejection = await recovery._claim(session, exhausted_body.model_copy(update={"idempotency_key": uuid4()}), operator_id=admin, now=NOW - timedelta(minutes=3))
            await session.commit()
        check("exhausted_expired_final_attempt_rejects_new_attempt", await reject(exhausted_body.model_copy(update={"idempotency_key": uuid4()}), "recovery_limit", apply=True))
        async with scoped() as session:
            last = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.id == last_claim["id"]))
            check("explicit_exhausted_apply_reconciles_expired_without_third_attempt", last.status == "failed" and last.error_code == "recovery_expired"
                and await session.scalar(select(func.count()).select_from(StockArchiveRecovery).where(StockArchiveRecovery.source_job_id == exhausted_job["id"])) == 2)
        future, future_job, future_body = await source(19, status="cancelled")
        check("before_close_today_source_cannot_be_manually_archived", await reject(future_body, "source_validation_failed", apply=True) and await count_recoveries(future_job["id"]) == 0)
        cli_source, cli_job, cli_body = await source(10)
        specification = importlib.util.spec_from_file_location("isolated_archive_recovery_cli", ROOT / "scripts/recover-stock-archive.py")
        cli = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cli)
        cli.create_replay_engine = lambda configured: create_async_engine(engine.url, hide_parameters=True,
            connect_args={"server_settings": {"search_path": namespace}})
        arguments = cli.parser().parse_args(["--source-job-id", str(cli_body.source_job_id), "--expected-data-version", cli_body.expected_data_version,
            "--idempotency-key", str(cli_body.idempotency_key), "--reason", cli_body.reason])
        original_token = os.environ.get("STOCK_ARCHIVE_RECOVERY_TOKEN")
        try:
            os.environ["STOCK_ARCHIVE_RECOVERY_TOKEN"] = create_access_token(settings, str(admin), "admin")
            cli_dry = await cli.run(arguments)
            check("actual_cli_default_read_only_does_not_write_attempt", cli_dry["status"] == "dry_run" and await count_recoveries(cli_job["id"]) == 0)
            arguments.apply = True
            cli_apply = await cli.run(arguments)
            check("actual_cli_explicit_apply_authenticates_checks_migrations_and_commits", cli_apply["status"] == "completed" and await count_recoveries(cli_job["id"]) == 1)
            for name, identity in (("ordinary", ordinary), ("frozen", frozen)):
                os.environ["STOCK_ARCHIVE_RECOVERY_TOKEN"] = create_access_token(settings, str(identity), "admin")
                try:
                    await cli.run(arguments)
                except (recovery.RecoveryError, HTTPException) as error:
                    check("actual_cli_rejects_" + name + "_even_with_admin_claim", (error.status if isinstance(error, recovery.RecoveryError) else error.status_code) == 403)
                else:
                    check("actual_cli_rejects_" + name, False)
            os.environ["STOCK_ARCHIVE_RECOVERY_TOKEN"] = str(admin)
            try:
                await cli.run(arguments)
            except HTTPException as error:
                check("actual_cli_rejects_bare_admin_uuid_not_authentication", error.status_code == 401)
            else:
                check("actual_cli_rejects_bare_admin_uuid", False)
        finally:
            if original_token is None:
                os.environ.pop("STOCK_ARCHIVE_RECOVERY_TOKEN", None)
            else:
                os.environ["STOCK_ARCHIVE_RECOVERY_TOKEN"] = original_token
        check("public_market_history_eod_catalog_jobs_archive_users_aggregates_unchanged", before == await fingerprint())
    finally:
        recovery.archive_eod = original_archive
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}" CASCADE'))
        async with engine.connect() as connection:
            report["cleanup"] = await connection.scalar(text("SELECT to_regnamespace(:namespace)"), {"namespace": namespace}) is None
        check("owned_private_schema_removed", report["cleanup"])
        report["public_aggregates_unchanged_after_cleanup"] = before == await fingerprint()
        await engine.dispose()
        with output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, allow_nan=False, indent=2)
        print("report", output.name, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
