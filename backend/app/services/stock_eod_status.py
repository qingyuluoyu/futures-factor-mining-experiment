from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.stock_eod import StockEodBatch
from app.services.stock_eod import METHOD_VERSION
from app.services.stock_eod_sync import read_catchup


STATUS_VERSION = "stock-eod-status-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MESSAGES = {
    "disabled": "盘后行情读取未启用，未将缺失配置当作已同步。",
    "unavailable": "盘后服务尚未就绪，请联系管理员核查配置或迁移。",
    "manual_only": "自动采集未启用，当前只展示已有快照。",
    "stopped": "自动采集服务未运行，不能保证后续收盘会自动更新。",
    "before_window": "尚未到北京时间16:30后的采集窗口；展示的是已有快照，不是今日收盘或实时行情。",
    "pending": "已进入盘后检查窗口，等待服务核验交易日历和领取任务；尚未认定今日收盘已更新。",
    "running": "当日盘后任务正在执行，完成前继续展示上一有效快照。",
    "closed": "来源交易日历确认当日休市，没有生成当日价格快照。",
    "completed": "当日盘后快照已保存；来源返回与字段校验不等于交易所独立认证。",
    "partially_completed": "当日快照已保存但存在缺价或目录未匹配，不能声称全市场完整覆盖。",
    "failed": "当日采集失败，保留最后有效快照；不会无限重试或填造行情。",
    "expired": "当日运行任务已超过期限，状态检查不会改写任务或重复请求来源，请联系管理员核查。",
}


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("任务时间无效")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _job_payload(job: dict | None) -> dict | None:
    if job is None:
        return None
    if job["status"] not in ("running", "completed", "partially_completed", "failed", "no_data"):
        raise ValueError("盘后任务状态无效")
    error_code = job.get("error_code")
    if error_code is not None and (not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", error_code)):
        error_code = "unknown_error"
    step = job["step"] if job["step"] in ("fetching", "running", "completed", "partially_completed", "failed", "no_data") else "unknown"
    return {"id": str(job["id"]), "trade_date": job["trade_date"].isoformat(), "status": job["status"], "step": step,
        "attempts": job["attempts"], "started_at": _aware(job["started_at"]).isoformat(),
        "deadline_at": _aware(job["deadline_at"]).isoformat(),
        "finished_at": _aware(job["finished_at"]).isoformat() if job.get("finished_at") is not None else None,
        "error_code": error_code}


def build_sync_status(*, now: datetime, enabled: bool, sync_enabled: bool, worker_running: bool,
        today_job: dict | None, latest_snapshot: dict | None, error_code: str | None = None) -> dict:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("状态检查时点必须包含时区")
    local = now.astimezone(SHANGHAI)
    job = _job_payload(today_job)
    if error_code:
        state = "unavailable"
    elif not enabled:
        state = "disabled"
    elif not sync_enabled:
        state = "manual_only"
    elif not worker_running:
        state = "stopped"
    elif job is not None:
        state = "closed" if job["status"] == "no_data" else job["status"]
        if state == "running" and _aware(today_job["deadline_at"]) <= now:
            state = "expired"
    else:
        state = "before_window" if (local.hour, local.minute) < (16, 30) else "pending"
    return {"status_version": STATUS_VERSION, "server_time": local.isoformat(), "timezone": "Asia/Shanghai",
        "collection_window": "16:30", "schedule_interval_seconds": 300, "enabled": enabled,
        "sync_enabled": sync_enabled, "worker_running": worker_running, "state": state, "message": MESSAGES[state],
        "today_job": job, "latest_snapshot": latest_snapshot,
        "verification": "stored_metadata_only_not_independent_certification"}


def latest_status_statement(today: date):
    payload = StockEodBatch.snapshot_json
    return select(StockEodBatch.trade_date, StockEodBatch.data_version,
        StockEodBatch.error_code.label("batch_note_code"),
        payload["data_version"].as_string().label("snapshot_version"),
        payload["method_version"].as_string().label("method_version"),
        payload["collected_at"].as_string().label("collected_at"),
        payload["provider"].as_string().label("provider"),
        payload["price_adjustment"].as_string().label("price_adjustment"),
        payload["expected_count"].as_integer().label("expected"),
        payload["covered_count"].as_integer().label("quoted"),
        payload["source_row_count"].as_integer().label("source_count"),
        payload["missing_codes"].label("missing_codes"), payload["unknown_codes"].label("unknown_codes"),
        payload["source_evidence"].label("source_evidence"),
    ).where(StockEodBatch.trade_date <= today, StockEodBatch.status.in_(("completed", "partially_completed"))).order_by(StockEodBatch.trade_date.desc()).limit(1)


def _has_raw_daily_evidence(evidence, source_count: int) -> bool:
    if not isinstance(evidence, dict) or evidence.get("version") != "teajoin-eod-response-v1" or evidence.get("mode") != "live_http":
        return False
    responses = evidence.get("responses")
    if not isinstance(responses, list) or not 1 <= len(responses) <= 3:
        return False
    daily = [item for item in responses if isinstance(item, dict) and item.get("api_name") == "daily"]
    if len(daily) != 1:
        return False
    item = daily[0]
    if (not isinstance(item.get("payload_sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["payload_sha256"])
            or type(item.get("byte_count")) is not int or not 1 <= item["byte_count"] <= 8 * 1024 * 1024
            or type(item.get("row_count")) is not int or item["row_count"] != source_count):
        return False
    try:
        received_at = datetime.fromisoformat(item["received_at"])
        return received_at.tzinfo is not None and received_at.utcoffset() is not None
    except (KeyError, ValueError, TypeError):
        return False


def _snapshot_payload(row) -> dict | None:
    if row is None:
        return None
    version = row["data_version"]
    if (not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version)
            or row["snapshot_version"] != version or row["method_version"] != METHOD_VERSION):
        raise ValueError("盘后快照元数据版本不一致")
    if any(type(row[name]) is not int or not 0 <= row[name] <= 10000 for name in ("expected", "quoted", "source_count")):
        raise ValueError("盘后覆盖数量无效")
    missing, unknown = row["missing_codes"], row["unknown_codes"]
    if any(not isinstance(items, list) or len(items) > 10000
            or any(not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) for code in items) for items in (missing, unknown)):
        raise ValueError("盘后缺口元数据无效")
    if row["quoted"] + len(missing) != row["expected"] or row["quoted"] + len(unknown) != row["source_count"]:
        raise ValueError("盘后覆盖元数据不一致")
    collected = datetime.fromisoformat(row["collected_at"])
    if collected.tzinfo is None or collected.utcoffset() is None:
        raise ValueError("快照采集时间缺少时区")
    if not isinstance(row["provider"], str) or len(row["provider"]) > 128 or row["price_adjustment"] != "none":
        raise ValueError("盘后来源或复权元数据无效")
    raw_evidence = _has_raw_daily_evidence(row["source_evidence"], row["source_count"])
    return {"trade_date": row["trade_date"].isoformat(), "data_version": version, "collected_at": collected.isoformat(),
        "provider": row["provider"], "price_adjustment": row["price_adjustment"],
        "coverage": {"expected": row["expected"], "quoted": row["quoted"], "missing": len(missing), "unknown": len(unknown)},
        "raw_response_evidence": raw_evidence, "recovery_applied": row.get("batch_note_code") == "eod_recovered"}


async def read_sync_status(session, *, now: datetime, enabled: bool, sync_enabled: bool, worker_running: bool, error_code: str | None = None, catchup_enabled: bool = False) -> dict:
    today_job, latest_snapshot, catchup = None, None, None
    if enabled and not error_code:
        today = now.astimezone(SHANGHAI).date()
        today_job = (await session.execute(select(StockEodBatch.id, StockEodBatch.trade_date, StockEodBatch.status, StockEodBatch.step,
            StockEodBatch.attempts, StockEodBatch.started_at, StockEodBatch.deadline_at, StockEodBatch.finished_at, StockEodBatch.error_code,
        ).where(StockEodBatch.trade_date == today).limit(1))).mappings().first()
        latest_snapshot = _snapshot_payload((await session.execute(latest_status_statement(today))).mappings().first())
        catchup = await read_catchup(session, now)
        catchup["automatic_enabled"] = catchup_enabled
    return {**build_sync_status(now=now, enabled=enabled, sync_enabled=sync_enabled, worker_running=worker_running,
        today_job=today_job, latest_snapshot=latest_snapshot, error_code=error_code), "catchup": catchup}
