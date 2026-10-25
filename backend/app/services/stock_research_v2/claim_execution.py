import logging

from app.schemas.stock_research_v2_jobs import DatasetClaim
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import ArchiveService
from app.services.stock_research_v2.artifacts import ArtifactStore
from app.services.stock_research_v2.jobs import fenced, jobs_ready, sources_snapshot

logger = logging.getLogger("qihuo.stock_dataset_claim_execution")


async def build_claim_dataset(session_factory, store: ArtifactStore, claim: DatasetClaim, *, columnar_store=None):
    claim = DatasetClaim.model_validate(claim.model_dump())
    async with session_factory() as session:
        try:
            if not await jobs_ready(session):
                raise JobError("v2_job_schema_unavailable", "观察任务结构尚未就绪", 503)
            job = await fenced(session, claim)
            if job is None:
                await session.rollback()
                return None
            if not isinstance(store, ArtifactStore) or not store.root.is_dir():
                raise JobError("artifact_store_unavailable", "固定观察存储根未就绪", 503)
            archive = ArchiveService(session, claim.scope, store)
            columnar = None
            if columnar_store is not None:
                from app.services.stock_research_v2.columnar import ColumnarService
                columnar = ColumnarService(archive, columnar_store)
                await columnar.require_ready()
            if await sources_snapshot(archive, claim.parameters) != job.parameters["sources"]:
                raise JobError("source_version_conflict", "任务固定来源元数据发生变化", 409)
            reference = await archive.build_dataset(claim.parameters, idempotency_key=claim.job_id, request_id=claim.request_id)
            manifest = await archive.read_dataset(reference)
            if manifest["sources"] != job.parameters["sources"]:
                raise JobError("source_version_conflict", "生成结果与任务固定来源不一致", 409)
            result = await columnar.build(reference, idempotency_key=claim.job_id, request_id=claim.request_id) if columnar else reference
            if await fenced(session, claim) is None:
                await session.rollback()
                return None
            await session.commit()
            logger.info("request_id=%s job_id=%s user_id=%s mode=%s operation=observation_registered_not_job_completed data_version=%s", claim.request_id, claim.job_id, claim.scope.owner_id, claim.scope.trading_mode, reference.data_version)
            return result
        except BaseException:
            await session.rollback()
            raise
