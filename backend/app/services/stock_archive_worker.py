import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import logging
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.models.stock_archive_job import StockArchiveJob
from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import METHOD_VERSION, archive_eod
from app.services.stock_eod import METHOD_VERSION as EOD_METHOD_VERSION


logger = logging.getLogger("qihuo.stock_archive_worker")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _aware(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("归档任务时点必须包含时区")
    return now.astimezone(timezone.utc)


def _last_date(now: datetime):
    local = _aware(now).astimezone(SHANGHAI)
    return local.date() - timedelta(days=1) if (local.hour, local.minute) < (16, 30) else local.date()


def eligibility_statement(now: datetime):
    recorded = exists(select(StockArchiveJob.id).where(StockArchiveJob.source_batch_id == StockEodBatch.id,
        StockArchiveJob.method_version == METHOD_VERSION))
    return select(StockEodBatch.id, StockEodBatch.trade_date, StockEodBatch.data_version).where(
        StockEodBatch.status.in_(("completed", "partially_completed")), StockEodBatch.finished_at.is_not(None),
        StockEodBatch.trade_date <= _last_date(now), StockEodBatch.method_version == EOD_METHOD_VERSION,
        StockEodBatch.data_version.is_not(None), ~recorded).order_by(StockEodBatch.trade_date, StockEodBatch.id).limit(1)


def new_job_values(source, *, now: datetime) -> dict:
    moment = _aware(now)
    if (not isinstance(source["id"], UUID) or not isinstance(source["data_version"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["data_version"]) or source["trade_date"] > _last_date(now)):
        raise ValueError("归档任务来源标识、日期或版本无效")
    return {"id": uuid4(), "source_batch_id": source["id"], "source_data_version": source["data_version"],
        "method_version": METHOD_VERSION, "trade_date": source["trade_date"], "request_id": uuid4().hex,
        "status": "running", "step": "archiving", "attempts": 1, "started_at": moment, "updated_at": moment,
        "deadline_at": moment + timedelta(seconds=90), "finished_at": None, "archive_batch_id": None,
        "error_code": None, "error_message": None}


async def _limits(session):
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))


async def _claim(session, *, now: datetime):
    expired = select(StockArchiveJob.id).where(StockArchiveJob.status == "running", StockArchiveJob.deadline_at <= now)
    expired = expired.order_by(StockArchiveJob.deadline_at).limit(10).with_for_update(skip_locked=True)
    await session.execute(update(StockArchiveJob).where(StockArchiveJob.id.in_(expired)).values(
        status="failed", step="failed", finished_at=now, updated_at=now, error_code="archive_expired",
        error_message="归档任务已超时；未自动重试或修改原EOD"))
    source = (await session.execute(eligibility_statement(now))).mappings().first()
    if source is None:
        return None
    values = new_job_values(source, now=now)
    identifier = (await session.execute(insert(StockArchiveJob).values(**values)
        .on_conflict_do_nothing().returning(StockArchiveJob.id))).scalar_one_or_none()
    return values if identifier is not None else None


async def _save_failure(factory, job, *, cancelled: bool, now: datetime):
    status = "cancelled" if cancelled else "failed"
    async with asyncio.timeout(10), factory() as session:
        await _limits(session)
        changed = (await session.execute(update(StockArchiveJob).where(StockArchiveJob.id == job["id"], StockArchiveJob.status == "running").values(
            status=status, step=status, updated_at=now, finished_at=now,
            error_code="archive_cancelled" if cancelled else "archive_validation_or_storage_failed",
            error_message="影子归档停止或未通过校验；原EOD保留，未自动重试").returning(StockArchiveJob.id))).scalar_one_or_none()
        await session.commit()
        return changed is not None


async def process_archive_once(factory, *, now: datetime | None = None) -> dict:
    moment = _aware(now if now is not None else datetime.now(timezone.utc))
    clock = (lambda: moment) if now is not None else (lambda: datetime.now(timezone.utc))
    job = None
    try:
        async with asyncio.timeout(10), factory() as session:
            await _limits(session)
            job = await _claim(session, now=moment)
            await session.commit()
        if job is None:
            return {"status": "idle", "source_calls": 0, "model_calls": 0}
        logger.info("request_id=%s job_id=%s source_batch_id=%s operation=archive_started attempts=1", job["request_id"], job["id"], job["source_batch_id"])
        async with asyncio.timeout(60), factory() as session:
            await _limits(session)
            stored = await session.scalar(select(StockArchiveJob).where(StockArchiveJob.id == job["id"]).with_for_update())
            if stored is None or stored.status != "running" or stored.deadline_at <= clock():
                raise ValueError("归档任务已停止或超过期限")
            source = await session.scalar(select(StockEodBatch).where(StockEodBatch.id == job["source_batch_id"]).with_for_update(read=True))
            if source is None or source.data_version != stored.source_data_version:
                raise ValueError("归档来源版本已改变")
            result = await archive_eod(session, source, now=clock())
            finished = clock()
            if stored.deadline_at <= finished:
                raise ValueError("归档结果已迟到，不提交影子记录")
            stored.status = stored.step = "completed"
            stored.updated_at = stored.finished_at = finished
            stored.archive_batch_id = UUID(result["id"])
            await session.commit()
        logger.info("request_id=%s job_id=%s operation=archive_completed rows=%s source_calls=0 model_calls=0", job["request_id"], job["id"], result["row_count"])
        return {"status": "completed", "job_id": str(job["id"]), "archive_batch_id": result["id"], "row_count": result["row_count"], "source_calls": 0, "model_calls": 0}
    except BaseException as error:
        if job is None:
            raise
        try:
            persisted = await _save_failure(factory, job, cancelled=isinstance(error, asyncio.CancelledError), now=clock())
        except Exception as persistence_error:
            logger.error("request_id=%s job_id=%s operation=archive_failure_save error_type=%s", job["request_id"], job["id"], type(persistence_error).__name__)
            if isinstance(error, asyncio.CancelledError):
                raise error from None
            raise RuntimeError("影子归档失败且状态保存失败，需核查持久任务") from None
        logger.error("request_id=%s job_id=%s operation=archive_stopped error_type=%s", job["request_id"], job["id"], type(error).__name__)
        if isinstance(error, asyncio.CancelledError):
            raise
        if not persisted:
            raise RuntimeError("归档领取未提交或已终止；未伪造失败任务") from None
        return {"status": "failed", "job_id": str(job["id"]), "source_calls": 0, "model_calls": 0}


class StockArchiveWorker:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.run(), name="stock-archive-worker")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def run(self):
        while True:
            try:
                await process_archive_once(self.session_factory)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("operation=archive_schedule error_type=%s", type(error).__name__)
            await asyncio.sleep(300)
