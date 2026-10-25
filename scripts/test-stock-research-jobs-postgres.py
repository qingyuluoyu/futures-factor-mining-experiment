import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_research_job import StockResearchJob
from app.models.user import User
from app.schemas.stock_research_jobs import CreateResearchJob
from app.services.stock_research_jobs import ResearchJobService, claim_next_job, recover_expired_jobs, release_worker_slot, utc_now
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def main():
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise RuntimeError("Only the dedicated local QA database is permitted")
    accounts = json.loads((ROOT / "logs/qa-local-accounts.json").read_text(encoding="utf-8"))
    assert len(accounts) == 2 and all(account["username"].startswith("qa_") for account in accounts)
    engine = create_replay_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    namespace = "qa_research_" + uuid4().hex
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(name, "PASS" if condition else "FAIL", flush=True)
        assert condition, name

    async def scoped():
        session = factory()
        await session.execute(text(f'SET search_path TO "{namespace}", public'))
        return session

    async def claim():
        async with await scoped() as session:
            job = await claim_next_job(session)
            await session.commit()
            return (job.id, job.worker_token) if job else None

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{namespace}"'))
            await connection.execute(text(f'CREATE TABLE "{namespace}".stock_research_jobs (LIKE public.stock_research_jobs INCLUDING ALL)'))
        async with await scoped() as session:
            users = (await session.execute(select(User.id).where(User.username.in_([account["username"] for account in accounts])))).scalars().all()
            assert len(users) == 2
            for user_id in users:
                body = TypeAdapter(CreateResearchJob).validate_python({"kind": "analysis", "idempotency_key": str(uuid4()), "parameters": {"factor_id": "ret_20d", "start_date": "20260713", "end_date": "20260911"}})
                await ResearchJobService(session, user_id, "virtual").create(body, request_id=uuid4().hex)
            await session.commit()
        claims = await asyncio.gather(claim(), claim(), claim(), claim())
        active = [item for item in claims if item is not None]
        check("four_concurrent_postgres_workers_claim_exactly_one_slot", len(active) == 1)
        job_id, token = active[0]
        async with await scoped() as session:
            job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == job_id))
            await ResearchJobService(session, job.user_id, "virtual").cancel(job_id)
            await session.commit()
        check("cancelled_slot_still_blocks_all_workers_before_ack", all(item is None for item in await asyncio.gather(claim(), claim())))
        async with await scoped() as session:
            check("wrong_worker_cannot_release_slot", not await release_worker_slot(session, job_id, uuid4()))
            check("stopped_worker_can_acknowledge_slot", await release_worker_slot(session, job_id, token))
            await session.commit()
        second = await claim()
        check("next_job_claimed_after_stop_ack", second is not None and second[0] != job_id)
        async with await scoped() as session:
            job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == second[0]))
            job.lease_expires_at = utc_now() - timedelta(seconds=1)
            await session.commit()
        check("lost_worker_lease_cannot_release_orphan_before_hard_deadline", await claim() is None)
        async with await scoped() as session:
            job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == second[0]))
            check("lost_worker_is_expired_without_retry", job.status == "expired" and job.attempts == 1 and job.worker_token == second[1] and job.result_json is None)
            job.deadline_at = utc_now() - timedelta(seconds=20)
            await session.commit()
            await recover_expired_jobs(session)
            await session.commit()
            check("orphan_slot_released_only_after_hard_deadline_grace", job.worker_token is None and job.status == "expired" and job.attempts == 1)
        (ROOT / "logs/stock-research-jobs-postgres-results-20261018.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP TABLE IF EXISTS "{namespace}".stock_research_jobs'))
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{namespace}"'))
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
