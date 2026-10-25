from datetime import datetime, timezone
import re

from sqlalchemy import select

from app.models.stock_archive_job import StockArchiveJob


FIELDS = ("id", "request_id", "source_batch_id", "source_data_version", "trade_date", "status", "step", "attempts",
    "started_at", "updated_at", "deadline_at", "finished_at", "archive_batch_id", "error_code")
MESSAGES = {
    "disabled": "影子归档自动任务未启用；没有把未开启当作已迁移。",
    "unavailable": "影子归档尚未就绪，请核查迁移或配置；原EOD采集独立保留。",
    "stopped": "影子归档后台未运行，无法保证后续快照自动归档。",
    "idle": "归档后台等待检查已有EOD，暂无已记录任务；不代表全部历史已迁移。",
    "running": "正在将已有原价快照写入独立小数成交量影子表，不请求行情来源。",
    "completed": "最近影子归档任务已完成；旧历史迁移、复权锚点和用户读路径未切换。",
    "failed": "最近影子归档失败；保留原EOD，不自动重试，需管理员核查。",
    "cancelled": "最近影子归档已取消；保留原EOD，不自动重试。",
    "expired": "归档任务已过期；状态读取不重写任务或触发重试，需后台核查。",
}


def _aware(value):
    if not isinstance(value, datetime):
        raise ValueError("归档任务时间无效")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def status_statement():
    return select(*(getattr(StockArchiveJob, name) for name in FIELDS)).order_by(StockArchiveJob.started_at.desc(), StockArchiveJob.id).limit(1)


def build_archive_status(*, now: datetime, enabled: bool, ready: bool, worker_running: bool, latest_job: dict | None) -> dict:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("归档状态时间必须包含时区")
    job = None
    if latest_job is not None:
        if latest_job["status"] not in ("running", "completed", "failed", "cancelled"):
            raise ValueError("归档任务状态无效")
        if (not re.fullmatch(r"[a-f0-9]{64}", latest_job["source_data_version"])
                or not re.fullmatch(r"[a-f0-9]{32}", latest_job["request_id"]) or latest_job["attempts"] != 1):
            raise ValueError("归档任务版本或追踪元数据无效")
        job = {name: latest_job[name] for name in FIELDS}
        for name in ("id", "source_batch_id", "archive_batch_id"):
            job[name] = str(job[name]) if job[name] is not None else None
        for name in ("started_at", "updated_at", "deadline_at", "finished_at"):
            job[name] = _aware(job[name]).isoformat() if job[name] is not None else None
        job["trade_date"] = job["trade_date"].isoformat()
        code = job["error_code"]
        if code is not None and (not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", code)):
            job["error_code"] = "unknown_error"
    if not enabled:
        state = "disabled"
    elif not ready:
        state = "unavailable"
    elif not worker_running:
        state = "stopped"
    elif job is None:
        state = "idle"
    else:
        state = job["status"]
        if state == "running" and _aware(latest_job["deadline_at"]) <= now:
            state = "expired"
    return {"status_version": "stock-archive-status-v1", "enabled": enabled, "ready": ready, "worker_running": worker_running,
        "state": state, "message": MESSAGES[state], "latest_job": job, "scope": "shadow_only_user_read_path_unchanged",
        "source_calls": 0, "model_calls": 0}


async def read_archive_status(session, *, now: datetime, enabled: bool, ready: bool, worker_running: bool) -> dict:
    job = (await session.execute(status_statement())).mappings().first() if enabled and ready else None
    return build_archive_status(now=now, enabled=enabled, ready=ready, worker_running=worker_running, latest_job=job)
