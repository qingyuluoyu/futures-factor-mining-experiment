import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, load_only

from app.models.stock_research_job import StockResearchJob
from app.models.user import User
from app.schemas.stock_research_jobs import (
    MAX_ACTIVE_JOBS, MAX_QUEUE_SECONDS, MAX_RESULT_BYTES, MAX_RUNTIME_SECONDS,
    MAX_USER_ACTIVE_JOBS, WORKFLOW_VERSION, CreateAnalysisJob, CreateBacktestJob,
    canonical_job_parameters, prepare_job_parameters,
)
from app.services.stock_factors.engine import METHOD_VERSION


ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "expired")
LEASE_SECONDS = 15


class JobError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso_datetime(value: datetime | None) -> str | None:
    return utc_datetime(value).isoformat() if value is not None else None


def job_view(job: StockResearchJob, *, details: bool = False) -> dict:
    view = {
        "id": str(job.id), "kind": job.kind, "title": job.title, "status": job.status,
        "step": job.step, "progress": job.progress, "request_id": job.request_id,
        "attempts": job.attempts, "created_at": iso_datetime(job.created_at),
        "updated_at": iso_datetime(job.updated_at), "started_at": iso_datetime(job.started_at),
        "finished_at": iso_datetime(job.finished_at), "method_version": job.method_version,
        "workflow_version": job.workflow_version, "data_version": job.data_version,
        "has_result": job.status == "completed",
        "error": {"code": job.error_code, "message": job.error_message, "status": job.error_status} if job.error_code else None,
    }
    if details:
        view.update(params=job.params_json, data_manifest=job.data_manifest)
    return view


async def queue_guard(session: AsyncSession) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(text("SET LOCAL statement_timeout = '10s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(20261018003)"))


def set_terminal(job: StockResearchJob, status: str, now: datetime, error: tuple[str, str, int] | None = None) -> None:
    if job.status not in ACTIVE_STATUSES or status not in TERMINAL_STATUSES:
        raise JobError("invalid_transition", "研究任务状态迁移不合法")
    was_running = job.status == "running"
    job.status, job.step, job.progress = status, status, 100
    job.updated_at = job.finished_at = now
    if not was_running:
        job.worker_token = job.lease_expires_at = None
    job.result_json, job.result_bytes = None, 0
    if error:
        job.error_code, job.error_message, job.error_status = error[0][:64], error[1][:500], error[2]


async def _recover_expired(session: AsyncSession, now: datetime) -> int:
    expired = or_(
        and_(StockResearchJob.status == "pending", StockResearchJob.created_at <= now - timedelta(seconds=MAX_QUEUE_SECONDS)),
        and_(StockResearchJob.status == "running", or_(StockResearchJob.lease_expires_at <= now, StockResearchJob.deadline_at <= now)),
    )
    jobs = (await session.execute(select(StockResearchJob).where(expired).order_by(StockResearchJob.created_at).with_for_update().limit(100))).scalars().all()
    for job in jobs:
        code = "queue_expired" if job.status == "pending" else "worker_lease_expired"
        set_terminal(job, "expired", now, (code, "任务等待或执行租约已过期；未自动重试，请核对后重新创建", 504))
    reserved = (await session.execute(select(StockResearchJob).options(defer(StockResearchJob.result_json)).where(
        StockResearchJob.status.in_(TERMINAL_STATUSES), StockResearchJob.worker_token.is_not(None),
        StockResearchJob.deadline_at <= now - timedelta(seconds=10),
    ).with_for_update().limit(100))).scalars().all()
    for job in reserved:
        job.worker_token = job.lease_expires_at = None
    await session.flush()
    return len(jobs)


async def recover_expired_jobs(session: AsyncSession) -> int:
    await queue_guard(session)
    return await _recover_expired(session, utc_now())


async def release_worker_slot(session: AsyncSession, job_id: UUID, worker_token: UUID) -> bool:
    await queue_guard(session)
    job = await session.scalar(select(StockResearchJob).options(defer(StockResearchJob.result_json)).where(StockResearchJob.id == job_id).with_for_update())
    if job is None or job.status not in TERMINAL_STATUSES or job.worker_token != worker_token:
        return False
    job.worker_token = job.lease_expires_at = None
    await session.flush()
    return True


class ResearchJobService:
    def __init__(self, session: AsyncSession, user_id: UUID, mode: str):
        if mode not in ("live", "virtual"):
            raise JobError("invalid_mode", "会话交易模式无效", 403)
        self.session, self.user_id, self.mode = session, user_id, mode

    def owned(self):
        return select(StockResearchJob).where(StockResearchJob.user_id == self.user_id, StockResearchJob.trading_mode == self.mode)

    async def row(self, job_id: UUID, *, lock: bool = False, include_result: bool = False) -> StockResearchJob:
        query = self.owned().where(StockResearchJob.id == job_id)
        if not include_result:
            query = query.options(defer(StockResearchJob.result_json))
        job = await self.session.scalar(query.with_for_update() if lock else query)
        if job is None:
            raise JobError("not_found", "研究任务不存在", 404)
        return job

    async def get(self, job_id: UUID) -> dict:
        return job_view(await self.row(job_id), details=True)

    async def create(self, body: CreateAnalysisJob | CreateBacktestJob, *, request_id: str, accepting_new: bool = True) -> dict:
        _, digest = canonical_job_parameters(body)
        await queue_guard(self.session)
        existing = await self.session.scalar(self.owned().options(defer(StockResearchJob.result_json)).where(StockResearchJob.idempotency_key == body.idempotency_key).with_for_update())
        if existing is not None:
            _, existing_digest = canonical_job_parameters(body, method_version=existing.method_version, workflow_version=existing.workflow_version)
            if existing.request_hash != existing_digest:
                raise JobError("idempotency_conflict", "同一幂等键已用于不同研究参数", 409)
            return job_view(existing, details=True)
        if not accepting_new:
            raise JobError("worker_unavailable", "研究执行器暂不可用，请保留幂等键稍后核对；未新建任务", 503)
        try:
            parameters, _ = prepare_job_parameters(body, today=datetime.now(ZoneInfo("Asia/Shanghai")).date())
        except ValueError as error:
            raise JobError("invalid_period", str(error), 422) from error
        await _recover_expired(self.session, utc_now())
        active = select(func.count()).select_from(StockResearchJob).where(StockResearchJob.status.in_(ACTIVE_STATUSES))
        user_active = await self.session.scalar(active.where(StockResearchJob.user_id == self.user_id, StockResearchJob.trading_mode == self.mode))
        if user_active >= MAX_USER_ACTIVE_JOBS:
            raise JobError("user_queue_full", "当前会话已有研究任务，请等待完成或先取消", 429)
        if await self.session.scalar(active) >= MAX_ACTIVE_JOBS:
            raise JobError("queue_full", "研究队列已满，请稍后重试", 429)
        now = utc_now()
        title = body.title or ("因子检验 " + parameters["factor_id"] if body.kind == "analysis" else "组合回测 " + "/".join(parameters["factor_ids"]))
        job = StockResearchJob(
            user_id=self.user_id, trading_mode=self.mode, kind=body.kind, title=title[:80],
            status="pending", step="queued", progress=0, request_id=request_id,
            idempotency_key=body.idempotency_key, request_hash=digest, params_json=parameters,
            method_version=METHOD_VERSION, workflow_version=WORKFLOW_VERSION, data_manifest={},
            attempts=0, result_bytes=0, created_at=now, updated_at=now,
        )
        self.session.add(job)
        await self.session.flush()
        return job_view(job, details=True)

    async def list(self, *, limit: int, offset: int, kind: str | None = None, status: str | None = None) -> dict:
        query = self.owned()
        if kind is not None:
            query = query.where(StockResearchJob.kind == kind)
        if status is not None:
            query = query.where(StockResearchJob.status == status)
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        query = query.options(load_only(
            StockResearchJob.id, StockResearchJob.kind, StockResearchJob.title, StockResearchJob.status,
            StockResearchJob.step, StockResearchJob.progress, StockResearchJob.request_id, StockResearchJob.attempts,
            StockResearchJob.created_at, StockResearchJob.updated_at, StockResearchJob.started_at, StockResearchJob.finished_at,
            StockResearchJob.method_version, StockResearchJob.workflow_version, StockResearchJob.data_version,
            StockResearchJob.error_code, StockResearchJob.error_message, StockResearchJob.error_status,
        ))
        rows = (await self.session.execute(query.order_by(StockResearchJob.created_at.desc(), StockResearchJob.id.desc()).offset(offset).limit(limit))).scalars().all()
        return {"items": [job_view(job) for job in rows], "total": total, "has_more": offset + len(rows) < total}

    async def cancel(self, job_id: UUID) -> dict:
        await queue_guard(self.session)
        job = await self.row(job_id, lock=True)
        if job.status == "cancelled":
            return job_view(job, details=True)
        if job.status not in ACTIVE_STATUSES:
            raise JobError("already_finished", "任务已结束，不能取消或更改已保存报告")
        set_terminal(job, "cancelled", utc_now(), ("user_cancelled", "用户已取消任务，未保存结果", 409))
        await self.session.flush()
        return job_view(job, details=True)

    async def result(self, job_id: UUID) -> dict:
        job = await self.row(job_id, include_result=True)
        if job.status != "completed" or job.result_json is None:
            raise JobError("result_unavailable", "研究尚未成功完成，请查看任务状态或错误详情")
        return {**job.result_json, "research_job_id": str(job.id), "data_version": job.data_version, "data_manifest": job.data_manifest, "workflow_version": job.workflow_version, "market": "stock", "research_only": True}


async def claim_next_job(session: AsyncSession) -> StockResearchJob | None:
    await queue_guard(session)
    now = utc_now()
    await _recover_expired(session, now)
    if await session.scalar(select(func.count()).select_from(StockResearchJob).where(StockResearchJob.worker_token.is_not(None))):
        return None
    job = await session.scalar(select(StockResearchJob).where(StockResearchJob.status == "pending").order_by(StockResearchJob.created_at, StockResearchJob.id).limit(1).with_for_update(skip_locked=True))
    if job is None:
        return None
    user_status = await session.scalar(select(User.status).where(User.id == job.user_id))
    if user_status != "active":
        set_terminal(job, "failed", now, ("user_not_active", "用户不存在或已被冻结，未执行研究", 403))
        await session.flush()
        return None
    if job.method_version != METHOD_VERSION or job.workflow_version != WORKFLOW_VERSION:
        set_terminal(job, "failed", now, ("method_changed", "计算方法已变更，旧排队任务未自动改用新版本执行", 409))
        await session.flush()
        return None
    job.status, job.step, job.progress = "running", "loading_snapshot", 10
    job.attempts, job.worker_token = 1, uuid4()
    job.started_at = job.updated_at = now
    job.deadline_at = now + timedelta(seconds=MAX_RUNTIME_SECONDS)
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    await session.flush()
    return job


async def heartbeat_job(session: AsyncSession, job_id: UUID, worker_token: UUID, manifest: dict | None = None) -> bool:
    await queue_guard(session)
    job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == job_id).with_for_update())
    now = utc_now()
    if job is None or job.status != "running" or job.worker_token != worker_token or utc_datetime(job.deadline_at) <= now or utc_datetime(job.lease_expires_at) <= now:
        return False
    job.updated_at = now
    job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    if manifest is not None:
        validate_manifest(manifest)
        job.data_manifest, job.data_version = manifest, manifest["data_version"]
        job.step, job.progress = "calculating", 50
    await session.flush()
    return True


def validate_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("data_version", ""))):
        raise JobError("invalid_provenance", "数据快照版本无效")
    try:
        encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as failure:
        raise JobError("invalid_provenance", "数据快照元数据无法序列化") from failure
    if len(encoded) > 512 * 1024:
        raise JobError("invalid_provenance", "数据快照元数据超过512KiB上限", 422)


async def finish_job(session: AsyncSession, job_id: UUID, worker_token: UUID, *, result: dict | None = None, manifest: dict | None = None, error: tuple[str, str, int] | None = None, terminal_status: str = "failed") -> bool:
    await queue_guard(session)
    job = await session.scalar(select(StockResearchJob).where(StockResearchJob.id == job_id).with_for_update())
    now = utc_now()
    if job is None or job.status != "running" or job.worker_token != worker_token:
        return False
    if utc_datetime(job.deadline_at) <= now or utc_datetime(job.lease_expires_at) <= now:
        set_terminal(job, "expired", now, ("execution_expired", "任务执行或租约已超时，迟到结果未保存", 504))
        await session.flush()
        return False
    if error is not None:
        if terminal_status not in ("failed", "expired"):
            raise JobError("invalid_transition", "执行器终态无效")
        set_terminal(job, terminal_status, now, error)
    else:
        manifest = manifest or job.data_manifest
        validate_manifest(manifest)
        if not isinstance(result, dict) or result.get("method_version") != job.method_version or result.get("validation_status") != "research_only" or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("data_version", ""))):
            raise JobError("invalid_result", "研究结果或数据版本未通过校验")
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            json.dumps(manifest, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as failure:
            raise JobError("invalid_result", "研究结果包含非有限值或无法序列化字段") from failure
        if len(encoded) > MAX_RESULT_BYTES:
            raise JobError("result_too_large", "研究结果超过8MiB安全上限", 422)
        set_terminal(job, "completed", now)
        job.result_json, job.result_bytes = result, len(encoded)
        job.data_manifest, job.data_version = manifest, manifest["data_version"]
    await session.flush()
    return True
