from datetime import timedelta
import hashlib
import logging
from pathlib import Path
import re
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import defer

from app.models.stock_research_v2_job import StockResearchV2Job as Job, StockResearchV2Worker as Worker, V2JobBase, V2JobSchemaVersion
from app.schemas.stock_research_v2 import DatasetBuild, DatasetRef, Scope
from app.schemas.stock_research_v2_jobs import ACTIVE, TERMINAL, METHOD_VERSION, WORKFLOW_VERSION, CreateDatasetJob, DatasetClaim, WorkerLease
from app.services.stock_research_jobs import JobError, iso_datetime, utc_datetime, utc_now
from app.services.stock_research_v2.archive import ArchiveService, datasets_ready, descriptor

SCHEMA_VERSION = "20261023_016_stock_research_v2_jobs"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / f"{SCHEMA_VERSION}.sql"
LEASE_SECONDS = 15
MAX_ACTIVE = 16
MAX_SCOPE_ACTIVE = 2
logger = logging.getLogger("qihuo.stock_research_v2_jobs")


async def jobs_ready(session):
    names = sorted(V2JobBase.metadata.tables)
    if session.get_bind().dialect.name == "postgresql":
        for name in names:
            if await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || :name)"), {"name": name}) is None:
                return False
    else:
        literals = ",".join("'" + name + "'" for name in names)
        if await session.scalar(text(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ({literals})")) != len(names):
            return False
    checksum = await session.scalar(select(V2JobSchemaVersion.checksum).where(V2JobSchemaVersion.version == SCHEMA_VERSION))
    if checksum != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
        return False
    for model in (Job, Worker):
        await session.execute(select(model).limit(0))
    return True


async def guard(session):
    if not await jobs_ready(session):
        raise JobError("v2_job_schema_unavailable", "股票v2任务尚未迁移或结构校验失败；未自动建表", 503)
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET LOCAL lock_timeout='5s'"))
        await session.execute(text("SET LOCAL statement_timeout='30s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(20261023016)"))


async def row(session, identifier):
    return await session.scalar(select(Job).where(Job.id == identifier).execution_options(populate_existing=True))


async def worker_row(session):
    return await session.scalar(select(Worker).where(Worker.name == WORKFLOW_VERSION).execution_options(populate_existing=True))


def live_worker(worker, now, lease=None):
    return worker is not None and utc_datetime(worker.lease_expires_at) > now and (lease is None or (worker.instance_id == lease.instance_id and worker.token == lease.token))


def terminal(job, status, now, code=None):
    if job.status not in ACTIVE or status not in TERMINAL:
        raise JobError("invalid_transition", "任务状态不可迁移", 409)
    job.status = job.step = status
    job.updated_at = job.finished_at = now
    job.error_code = code
    job.result_dataset_id = job.result_data_version = job.result_qualification = None


async def recover_expired(session):
    await guard(session)
    now, count = utc_now(), 0
    active = (await session.execute(select(Job).where(Job.status.in_(ACTIVE)).order_by(Job.created_at, Job.id).limit(100).execution_options(populate_existing=True))).scalars().all()
    for job in active:
        if job.status == "pending" and utc_datetime(job.queue_expires_at) <= now:
            terminal(job, "expired", now, "queue_expired")
            count += 1
        elif job.status == "running" and (utc_datetime(job.deadline_at) <= now or utc_datetime(job.lease_expires_at) <= now):
            terminal(job, "expired", now, "execution_expired")
            count += 1
    await session.flush()
    return count


def view(job, *, details=True):
    reference = DatasetRef(dataset_id=job.result_dataset_id, data_version=job.result_data_version, qualification=job.result_qualification).model_dump(mode="json") if job.status == "completed" else None
    result = {"id": str(job.id), "kind": job.kind, "status": job.status, "step": job.step, "attempts": job.attempts, "request_id": job.request_id,
        "created_at": iso_datetime(job.created_at), "updated_at": iso_datetime(job.updated_at), "started_at": iso_datetime(job.started_at), "finished_at": iso_datetime(job.finished_at),
        "deadline_at": iso_datetime(job.deadline_at), "queue_expires_at": iso_datetime(job.queue_expires_at),
        "method_version": job.method_version, "workflow_version": job.workflow_version, "result": reference, "error_code": job.error_code}
    if details:
        result["parameters"] = job.parameters["command"]
    return result


async def sources_snapshot(archive, command):
    await archive.require_ready()
    sources = await archive.source_rows(command.source_ids)
    if any(artifact.state != "ready" or artifact.purpose != "source" for source, artifact in sources):
        raise JobError("source_quarantined", "所选来源未就绪或已隔离", 409)
    return [{"source_id": str(source.id), "sha256": artifact.sha256, "byte_size": artifact.byte_size, "row_count": source.row_count, "kind": source.kind} for source, artifact in sources]


class JobService:
    def __init__(self, session, scope: Scope):
        self.session, self.scope = session, Scope.model_validate(scope.model_dump())

    def owned(self):
        return select(Job).where(Job.owner_id == self.scope.owner_id, Job.trading_mode == self.scope.trading_mode)

    async def available(self):
        if not await jobs_ready(self.session) or not await datasets_ready(self.session):
            return False
        return live_worker(await worker_row(self.session), utc_now())

    async def get(self, identifier):
        if not isinstance(identifier, UUID):
            raise JobError("invalid_job_id", "任务ID无效", 422)
        if not await jobs_ready(self.session):
            raise JobError("v2_job_schema_unavailable", "股票v2任务尚未迁移", 503)
        job = await self.session.scalar(self.owned().where(Job.id == identifier).execution_options(populate_existing=True))
        if job is None:
            raise JobError("job_not_found", "任务不存在", 404)
        return view(job)

    async def list(self, *, limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise JobError("invalid_pagination", "任务分页超出范围", 422)
        if not await jobs_ready(self.session):
            raise JobError("v2_job_schema_unavailable", "股票v2任务尚未迁移", 503)
        total = await self.session.scalar(self.owned().with_only_columns(func.count(), maintain_column_froms=True))
        rows = (await self.session.execute(self.owned().options(defer(Job.parameters, raiseload=True)).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).offset(offset).execution_options(populate_existing=True))).scalars().all()
        return {"items": [view(job, details=False) for job in rows], "total": total, "has_more": offset + len(rows) < total}

    async def submit(self, command: CreateDatasetJob, *, request_id):
        command = CreateDatasetJob.model_validate(command.model_dump())
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise JobError("invalid_request_id", "请求号无效", 422)
        digest, _ = descriptor({**command.model_dump(mode="json", exclude={"idempotency_key"}), "method_version": METHOD_VERSION, "workflow_version": WORKFLOW_VERSION}, 512 * 1024)
        await guard(self.session)
        existing = await self.session.scalar(self.owned().where(Job.idempotency_key == command.idempotency_key).execution_options(populate_existing=True))
        if existing is not None:
            if existing.request_hash != digest:
                raise JobError("idempotency_conflict", "幂等键已用于不同参数或方法版本", 409)
            return view(existing)
        if not await self.available():
            raise JobError("worker_unavailable", "无有效v2观察数据执行器租约；没有新建任务", 503)
        snapshot = await sources_snapshot(ArchiveService(self.session, self.scope), command.parameters)
        await recover_expired(self.session)
        active = select(func.count()).select_from(Job).where(Job.status.in_(ACTIVE))
        if await self.session.scalar(active.where(Job.owner_id == self.scope.owner_id, Job.trading_mode == self.scope.trading_mode)) >= MAX_SCOPE_ACTIVE:
            raise JobError("scope_queue_full", "当前用户盘已有两个活动任务；业务总次数不限", 429)
        if await self.session.scalar(active) >= MAX_ACTIVE:
            raise JobError("queue_full", "研究队列暂满", 429)
        now = utc_now()
        job = Job(id=uuid4(), owner_id=self.scope.owner_id, trading_mode=self.scope.trading_mode, idempotency_key=command.idempotency_key, request_hash=digest, request_id=request_id, kind=command.kind,
            status="pending", step="queued", parameters={"command": command.parameters.model_dump(mode="json"), "sources": snapshot}, method_version=METHOD_VERSION, workflow_version=WORKFLOW_VERSION,
            max_runtime_seconds=command.max_runtime_seconds, max_queue_seconds=command.max_queue_seconds, attempts=0, created_at=now, updated_at=now, queue_expires_at=now + timedelta(seconds=command.max_queue_seconds))
        self.session.add(job)
        await self.session.flush()
        logger.info("request_id=%s job_id=%s user_id=%s mode=%s step=prepared_not_committed workflow=%s", request_id, job.id, self.scope.owner_id, self.scope.trading_mode, WORKFLOW_VERSION)
        return view(job)

    async def cancel(self, identifier):
        await guard(self.session)
        await self.get(identifier)
        job = await row(self.session, identifier)
        if job.status == "cancelled":
            return view(job)
        if job.status not in ACTIVE:
            raise JobError("already_finished", "任务已结束，不可取消或覆盖结果", 409)
        terminal(job, "cancelled", utc_now(), "user_cancelled")
        await self.session.flush()
        return view(job)


async def acquire_worker(session, instance_id):
    if not isinstance(instance_id, UUID):
        raise JobError("invalid_worker_identity", "执行器身份无效", 422)
    await guard(session)
    if not await datasets_ready(session):
        raise JobError("dataset_schema_unavailable", "固定数据归档未迁移", 503)
    await recover_expired(session)
    now = utc_now()
    worker = await worker_row(session)
    if live_worker(worker, now):
        raise JobError("worker_already_active", "已有有效执行器；不抢占其租约", 409)
    lease = WorkerLease(instance_id=instance_id, token=uuid4())
    if worker is None:
        worker = Worker(name=WORKFLOW_VERSION)
        session.add(worker)
    worker.instance_id, worker.token = instance_id, lease.token
    worker.heartbeat_at, worker.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
    worker.busy_job_id = None
    await session.flush()
    return lease


async def claim_next(session, lease: WorkerLease):
    lease = WorkerLease.model_validate(lease.model_dump())
    await guard(session)
    await recover_expired(session)
    now, worker = utc_now(), await worker_row(session)
    if not live_worker(worker, now, lease):
        raise JobError("worker_lease_lost", "执行器租约失效，禁止领取任务", 409)
    if worker.busy_job_id is not None:
        return None
    job = await session.scalar(select(Job).where(Job.status == "pending", Job.kind == "dataset", Job.workflow_version == WORKFLOW_VERSION).order_by(Job.created_at, Job.id).limit(1).with_for_update(skip_locked=True).execution_options(populate_existing=True))
    if job is None:
        return None
    scope = Scope(owner_id=job.owner_id, trading_mode=job.trading_mode)
    try:
        submitted = CreateDatasetJob(idempotency_key=job.idempotency_key, parameters=job.parameters["command"], max_runtime_seconds=job.max_runtime_seconds, max_queue_seconds=job.max_queue_seconds)
        command = submitted.parameters
        digest, _ = descriptor({**submitted.model_dump(mode="json", exclude={"idempotency_key"}), "method_version": job.method_version, "workflow_version": job.workflow_version}, 512 * 1024)
        if digest != job.request_hash or job.method_version != METHOD_VERSION:
            raise JobError("job_input_integrity_failed", "任务参数或方法指纹变化", 409)
        snapshot = await sources_snapshot(ArchiveService(session, scope), command)
        if snapshot != job.parameters["sources"]:
            raise JobError("source_version_conflict", "固定来源登记变化", 409)
    except JobError as error:
        terminal(job, "failed", now, error.code)
        await session.flush()
        return None
    except (KeyError, TypeError, ValidationError):
        terminal(job, "failed", now, "job_input_integrity_failed")
        await session.flush()
        return None
    job.status, job.step, job.attempts = "running", "claimed", 1
    job.started_at = job.updated_at = now
    job.deadline_at = now + timedelta(seconds=job.max_runtime_seconds)
    job.lease_expires_at = min(utc_datetime(worker.lease_expires_at), job.deadline_at)
    job.worker_token = uuid4()
    worker.busy_job_id = job.id
    await session.flush()
    return DatasetClaim(job_id=job.id, scope=scope, worker=lease, token=job.worker_token, parameters=command, request_id=job.request_id, deadline_at=job.deadline_at)


async def fenced(session, claim):
    job, worker, now = await row(session, claim.job_id), await worker_row(session), utc_now()
    valid = job is not None and job.owner_id == claim.scope.owner_id and job.trading_mode == claim.scope.trading_mode and job.worker_token == claim.token and job.status == "running"
    if not valid or not live_worker(worker, now, claim.worker) or worker.busy_job_id != job.id:
        return None
    if utc_datetime(job.deadline_at) <= now or utc_datetime(job.lease_expires_at) <= now:
        return None
    if job.parameters["command"] != claim.parameters.model_dump(mode="json") or job.request_id != claim.request_id or utc_datetime(job.deadline_at) != claim.deadline_at:
        return None
    return job


async def renew_claim(session, claim: DatasetClaim):
    await guard(session)
    job = await fenced(session, claim)
    if job is None:
        return False
    now, worker = utc_now(), await worker_row(session)
    worker.heartbeat_at, worker.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
    job.updated_at, job.lease_expires_at = now, min(worker.lease_expires_at, utc_datetime(job.deadline_at))
    await session.flush()
    return True


async def heartbeat_worker(session, lease: WorkerLease):
    lease = WorkerLease.model_validate(lease.model_dump())
    await guard(session)
    worker, now = await worker_row(session), utc_now()
    if not live_worker(worker, now, lease) or worker.busy_job_id is not None:
        return False
    worker.heartbeat_at, worker.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
    await session.flush()
    return True


async def fail_claim(session, claim: DatasetClaim, code):
    if code not in {"worker_failed", "dataset_invalid", "execution_timeout"}:
        raise JobError("invalid_failure_code", "不可持久化任意异常或模型文本", 422)
    await guard(session)
    job = await fenced(session, claim)
    if job is None:
        return False
    terminal(job, "failed", utc_now(), code)
    await session.flush()
    return True


async def finish_dataset(session, claim: DatasetClaim, reference, *, archive=None):
    await guard(session)
    job = await fenced(session, claim)
    if job is None:
        return False
    if not isinstance(archive, ArchiveService) or archive.session is not session or archive.scope != claim.scope:
        raise JobError("result_archive_scope_mismatch", "结果归档上下文不匹配", 409)
    fixed = DatasetRef.model_validate(reference)
    manifest = await archive.read_dataset(fixed)
    declared = {**{key: value for key, value in manifest.items() if key not in {"sources", "manifest_version", "qualification"}}, "source_ids": [UUID(item["source_id"]) for item in manifest["sources"]]}
    if DatasetBuild.model_validate(declared) != claim.parameters or manifest["sources"] != job.parameters["sources"] or fixed.qualification != "observation_only":
        raise JobError("job_result_mismatch", "结果未对应任务固定参数及来源指纹", 409)
    job = await fenced(session, claim)
    if job is None:
        return False
    terminal(job, "completed", utc_now())
    job.result_dataset_id, job.result_data_version, job.result_qualification = fixed.dataset_id, fixed.data_version, fixed.qualification
    await session.flush()
    logger.info("request_id=%s job_id=%s user_id=%s mode=%s step=result_prepared_not_committed data_version=%s", claim.request_id, claim.job_id, claim.scope.owner_id, claim.scope.trading_mode, fixed.data_version)
    return True


async def release_claim(session, claim: DatasetClaim):
    await guard(session)
    worker, job = await worker_row(session), await row(session, claim.job_id)
    if not live_worker(worker, utc_now(), claim.worker) or worker.busy_job_id != claim.job_id or job is None or job.worker_token != claim.token:
        return False
    if job.status not in TERMINAL:
        raise JobError("claim_still_running", "运行中任务不可释放槽位；先确认进程结束及失败状态", 409)
    worker.busy_job_id = None
    job.worker_token = job.lease_expires_at = None
    await session.flush()
    return True
