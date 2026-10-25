import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_research_jobs as job_api
from app.core.deps import get_settings
from app.models.stock_research_job import StockResearchJob, StockResearchSchemaVersion
from app.services import stock_research_jobs as jobs
from tests.test_stock_research_job_service import creation


async def additional_checks(*, check, scoped, application, client, headers, other_id, stock_id, settings, **kwargs):
    application.include_router(job_api.router)
    application.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"stock_research_jobs_enabled": True})
    async with scoped() as session:
        session.add(StockResearchSchemaVersion(version=job_api.SCHEMA_VERSION, checksum=hashlib.sha256(job_api.SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()
    for index, first_owner in enumerate((stock_id, other_id)):
        second_owner = other_id if first_owner == stock_id else stock_id
        now = datetime.now(timezone.utc)
        base_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-000000000000").int + index * 2
        async with scoped() as session:
            with patch.object(jobs, "utc_now", return_value=now):
                first_created = await jobs.ResearchJobService(session, first_owner, "virtual").create(creation(), request_id=uuid4().hex)
                second_created = await jobs.ResearchJobService(session, second_owner, "virtual").create(creation(), request_id=uuid4().hex)
            first_row = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == UUID(first_created["id"])))
            second_row = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == UUID(second_created["id"])))
            first_row.id, second_row.id = UUID(int=base_id + 1), UUID(int=base_id + 2)
            await session.commit()
            check(f"night_{index}_same_time_jobs", first_row.created_at == second_row.created_at)
            claimed = await jobs.claim_next_job(session)
            await session.commit()
            job_id, worker_token = claimed.id, claimed.worker_token
            check(f"night_{index}_claim_by_timestamp_and_id", claimed.user_id == first_owner and job_id == first_row.id)
        url = f"/api/stock/research/jobs/{job_id}/cancel"
        response = await client.post(url, headers=headers(second_owner))
        check(f"night_{index}_wrong_owner_http404", response.status_code == 404 and response.headers.get("X-Research-Error") == "not_found")
        response = await client.post(url, headers=headers(first_owner, "live"))
        check(f"night_{index}_wrong_login_mode_http404", response.status_code == 404)
        check(f"night_{index}_anonymous_http401", (await client.post(url)).status_code == 401)
        async with scoped() as session:
            current = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == job_id))
            check(f"night_{index}_denied_cancel_keeps_state", current.status == "running" and current.worker_token == worker_token)
        response = await client.post(url, headers=headers(first_owner))
        check(f"night_{index}_owner_cancel_has_trace", response.status_code == 200 and response.json()["status"] == "cancelled" and bool(response.headers.get("X-Request-ID")))
        async with scoped() as session:
            check(f"night_{index}_cancel_keeps_compute_slot", await jobs.claim_next_job(session) is None)
            check(f"night_{index}_wrong_worker_does_not_release", not await jobs.release_worker_slot(session, job_id, uuid4()))
            check(f"night_{index}_matching_worker_releases", await jobs.release_worker_slot(session, job_id, worker_token))
            next_job = await jobs.claim_next_job(session)
            check(f"night_{index}_next_owner_can_run", next_job is not None and next_job.user_id == second_owner)
            await jobs.ResearchJobService(session, second_owner, "virtual").cancel(next_job.id)
            check(f"night_{index}_cleanup_worker_slot", await jobs.release_worker_slot(session, next_job.id, next_job.worker_token))
            await session.commit()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="night"))
