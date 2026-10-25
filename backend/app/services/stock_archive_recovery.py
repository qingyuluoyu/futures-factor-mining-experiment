import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.models.stock_archive_job import StockArchiveJob
from app.models.stock_archive_recovery import StockArchiveRecovery
from app.models.stock_eod import StockEodBatch
from app.models.user import User
from app.schemas.stock_archive_recovery import RecoveryRequest
from app.services.stock_daily_archive import METHOD_VERSION as ARCHIVE_METHOD, archive_eod, prepare_archive


METHOD_VERSION = "stock-archive-recovery-2026-10-19.v1"
logger = logging.getLogger("qihuo.stock_archive_recovery")


class RecoveryError(Exception):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code = code
        self.status = status


def _aware(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("恢复时点必须包含时区")
    return now.astimezone(timezone.utc)


def request_hash(body: RecoveryRequest) -> str:
    value = {"source_job_id": str(body.source_job_id), "expected_data_version": body.expected_data_version,
        "reason": body.reason, "method_version": METHOD_VERSION}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _values(record) -> dict:
    return record if isinstance(record, dict) else {column.name: getattr(record, column.name) for column in StockArchiveRecovery.__table__.columns}


def recovery_report(record, *, now: datetime) -> dict:
    value = _values(record)
    status = "expired" if value["status"] == "running" and value["deadline_at"] <= _aware(now) else value["status"]
    return {"id": str(value["id"]), "source_job_id": str(value["source_job_id"]),
        "source_batch_id": str(value["source_batch_id"]), "source_data_version": value["source_data_version"],
        "status": status, "attempt_number": value["attempt_number"], "request_id": value["request_id"],
        "archive_batch_id": str(value["archive_batch_id"]) if value["archive_batch_id"] else None,
        "error_code": "recovery_expired" if status == "expired" else value["error_code"],
        "source_calls": 0, "model_calls": 0, "user_read_path_changed": False}


async def _limits(session):
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))


async def _require_admin(session, operator_id: UUID, *, lock: bool = False):
    statement = select(User.id).where(User.id == operator_id, User.role == "admin", User.status == "active")
    if await session.scalar(statement.with_for_update(read=True) if lock else statement) is None:
        raise RecoveryError("admin_required", 403, "需要有效且未冻结的管理员身份")


async def _existing(session, body, operator_id):
    record = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.operator_id == operator_id,
        StockArchiveRecovery.idempotency_key == body.idempotency_key).limit(1))
    if record is not None and (record.request_hash != request_hash(body) or record.method_version != METHOD_VERSION):
        raise RecoveryError("idempotency_conflict", 409, "幂等键已用于不同请求")
    return record


async def _validate(session, body, *, now, lock, enforce_limit=True):
    statement = select(StockArchiveJob).where(StockArchiveJob.id == body.source_job_id)
    job = await session.scalar(statement.with_for_update() if lock else statement)
    if job is None:
        raise RecoveryError("source_job_not_found", 404, "原归档任务不存在")
    if job.status not in ("failed", "cancelled") or job.method_version != ARCHIVE_METHOD or job.archive_batch_id is not None:
        raise RecoveryError("source_job_not_recoverable", 409, "仅允许恢复已终止失败或取消的当前版本归档任务")
    statement = select(StockEodBatch).where(StockEodBatch.id == job.source_batch_id)
    source = await session.scalar(statement.with_for_update(read=True) if lock else statement)
    if (source is None or source.data_version != body.expected_data_version
            or source.data_version != job.source_data_version or source.trade_date != job.trade_date):
        raise RecoveryError("source_version_changed", 409, "原任务与当前来源版本或日期不一致")
    try:
        prepared = prepare_archive(source, now=now)
    except (ValueError, TypeError, KeyError):
        raise RecoveryError("source_validation_failed", 422, "来源快照未通过归档校验，禁止消耗恢复次数") from None
    statement = select(StockArchiveRecovery).where(StockArchiveRecovery.source_job_id == job.id).order_by(StockArchiveRecovery.attempt_number).limit(3)
    records = list((await session.scalars(statement)).all())
    if lock and any(item.status == "running" and item.deadline_at <= now for item in records):
        records = list((await session.scalars(statement.with_for_update().execution_options(populate_existing=True))).all())
    if any(item.status == "completed" for item in records):
        raise RecoveryError("already_recovered", 409, "该来源任务已恢复，不再接受新幂等键")
    if any(item.status == "running" and item.deadline_at > now for item in records):
        raise RecoveryError("recovery_running", 409, "已有恢复任务执行中")
    if enforce_limit and len(records) >= 2:
        raise RecoveryError("recovery_limit", 409, "该来源任务已用完两次人工恢复次数")
    return job, source, prepared, records


async def inspect_recovery(session, body: RecoveryRequest, *, operator_id: UUID, now: datetime) -> dict:
    moment = _aware(now)
    await _require_admin(session, operator_id)
    existing = await _existing(session, body, operator_id)
    if existing is not None:
        return recovery_report(existing, now=moment)
    job, source, prepared, records = await _validate(session, body, now=moment, lock=False)
    return {"status": "dry_run", "source_job_id": str(job.id), "source_batch_id": str(source.id),
        "source_data_version": source.data_version, "next_attempt_number": len(records) + 1,
        "row_count": prepared["batch"]["row_count"], "source_calls": 0, "model_calls": 0,
        "user_read_path_changed": False, "available_at_inferred": False}


async def _claim(session, body, *, operator_id, now):
    await _require_admin(session, operator_id, lock=True)
    existing = await _existing(session, body, operator_id)
    if existing is not None:
        return _values(existing), False, None
    await session.scalar(select(StockArchiveJob.id).where(StockArchiveJob.id == body.source_job_id).with_for_update())
    existing = await _existing(session, body, operator_id)
    if existing is not None:
        return _values(existing), False, None
    job, source, prepared, records = await _validate(session, body, now=now, lock=True, enforce_limit=False)
    for record in records:
        if record.status == "running":
            record.status = record.step = "failed"
            record.updated_at = record.finished_at = now
            record.error_code = "recovery_expired"
            record.error_message = "人工恢复超时，未自动重新执行"
    if len(records) >= 2:
        return None, False, RecoveryError("recovery_limit", 409, "该来源任务已用完两次人工恢复次数")
    values = {"id": uuid4(), "source_job_id": job.id, "source_batch_id": source.id,
        "source_data_version": source.data_version, "method_version": METHOD_VERSION, "operator_id": operator_id,
        "idempotency_key": body.idempotency_key, "request_hash": request_hash(body), "request_id": uuid4().hex,
        "reason": body.reason, "attempt_number": len(records) + 1, "status": "running", "step": "recovering",
        "started_at": now, "updated_at": now, "deadline_at": now + timedelta(seconds=90),
        "finished_at": None, "archive_batch_id": None, "error_code": None, "error_message": None}
    identifier = (await session.execute(insert(StockArchiveRecovery).values(**values).on_conflict_do_nothing()
        .returning(StockArchiveRecovery.id))).scalar_one_or_none()
    if identifier is None:
        existing = await _existing(session, body, operator_id)
        if existing is None:
            raise RecoveryError("recovery_conflict", 409, "恢复记录并发冲突，请先核查任务")
        return _values(existing), False, None
    return values, True, None


async def _save_failure(factory, job, *, cancelled, now):
    status = "cancelled" if cancelled else "failed"
    async with asyncio.timeout(10), factory() as session:
        await _limits(session)
        await session.execute(update(StockArchiveRecovery).where(StockArchiveRecovery.id == job["id"],
            StockArchiveRecovery.status == "running").values(status=status, step=status, updated_at=now, finished_at=now,
                error_code="recovery_cancelled" if cancelled else "recovery_validation_or_storage_failed",
                error_message="人工影子恢复停止或失败，原数据与原任务保留"))
        await session.commit()
    async with asyncio.timeout(10), factory() as session:
        await _limits(session)
        stored = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.id == job["id"]))
        if stored is None:
            raise RuntimeError("恢复领取未提交，未伪造失败记录")
        return recovery_report(stored, now=now)


async def run_recovery(factory, body: RecoveryRequest, *, operator_id: UUID, now: datetime | None = None) -> dict:
    moment = _aware(now if now is not None else datetime.now(timezone.utc))
    clock = (lambda: moment) if now is not None else (lambda: datetime.now(timezone.utc))
    job = None
    try:
        async with asyncio.timeout(10), factory() as session:
            await _limits(session)
            candidate, claimed, rejection = await _claim(session, body, operator_id=operator_id, now=moment)
            if claimed:
                job = candidate
            await session.commit()
        if rejection is not None:
            raise rejection
        if not claimed:
            return recovery_report(candidate, now=clock())
        logger.info("request_id=%s recovery_id=%s source_job_id=%s operation=recovery_started attempt=%s", job["request_id"], job["id"], job["source_job_id"], job["attempt_number"])
        async with asyncio.timeout(60), factory() as session:
            await _limits(session)
            await _require_admin(session, operator_id, lock=True)
            stored = await session.scalar(select(StockArchiveRecovery).where(StockArchiveRecovery.id == job["id"]).with_for_update())
            if stored is None or stored.status != "running" or stored.deadline_at <= clock():
                raise RecoveryError("recovery_stopped", 409, "恢复任务已停止或超时")
            source = await session.scalar(select(StockEodBatch).where(StockEodBatch.id == job["source_batch_id"]).with_for_update(read=True))
            if source is None or source.data_version != stored.source_data_version:
                raise RecoveryError("source_version_changed", 409, "恢复来源版本已改变")
            result = await archive_eod(session, source, now=clock())
            finished = clock()
            if stored.deadline_at <= finished:
                raise RecoveryError("recovery_expired", 409, "恢复结果已迟到，不提交")
            stored.status = stored.step = "completed"
            stored.updated_at = stored.finished_at = finished
            stored.archive_batch_id = UUID(result["id"])
            await session.commit()
            report = recovery_report(stored, now=finished)
        logger.info("request_id=%s recovery_id=%s operation=recovery_completed rows=%s source_calls=0 model_calls=0", job["request_id"], job["id"], result["row_count"])
        return report
    except BaseException as error:
        if job is None:
            raise
        try:
            report = await _save_failure(factory, job, cancelled=isinstance(error, asyncio.CancelledError), now=clock())
        except Exception as persistence_error:
            logger.error("request_id=%s recovery_id=%s operation=recovery_failure_save error_type=%s", job["request_id"], job["id"], type(persistence_error).__name__)
            if isinstance(error, asyncio.CancelledError):
                raise error from None
            raise RuntimeError("恢复失败且状态保存失败，需核查持久任务") from None
        logger.error("request_id=%s recovery_id=%s operation=recovery_stopped error_type=%s", job["request_id"], job["id"], type(error).__name__)
        if isinstance(error, asyncio.CancelledError):
            raise
        return report
