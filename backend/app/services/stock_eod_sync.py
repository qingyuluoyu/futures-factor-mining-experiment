import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text

from app.models.stock_eod import StockEodBatch
from app.services.stock_catalog import normalize_stock_catalog, upsert_stock_catalog
from app.services.stock_eod import METHOD_VERSION, content_hash, normalize_eod


logger = logging.getLogger("qihuo.stock_eod")
CATCHUP_DAYS = 7


def catchup_dates(now: datetime) -> list[date]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("盘后调度时点必须包含时区")
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    last = local.date() if (local.hour, local.minute) >= (16, 30) else local.date() - timedelta(days=1)
    return [last - timedelta(days=offset) for offset in range(CATCHUP_DAYS)]


async def read_catchup(session, now: datetime) -> dict:
    dates = catchup_dates(now)
    rows = (await session.execute(select(StockEodBatch.trade_date, StockEodBatch.status, StockEodBatch.deadline_at).where(
        StockEodBatch.trade_date >= dates[-1], StockEodBatch.trade_date <= dates[0],
    ).order_by(StockEodBatch.trade_date.desc()).limit(CATCHUP_DAYS))).all()
    recorded = {row.trade_date for row in rows}
    expired = [row.trade_date.isoformat() for row in rows if row.status == "running" and
        (row.deadline_at.replace(tzinfo=timezone.utc) if row.deadline_at.tzinfo is None else row.deadline_at) <= now]
    return {"policy_version": "stock-eod-catchup-v1", "window_start": dates[-1].isoformat(), "window_end": dates[0].isoformat(),
        "pending_dates": [day.isoformat() for day in dates if day not in recorded],
        "failed_dates": [row.trade_date.isoformat() for row in rows if row.status == "failed"], "expired_dates": expired,
        "calendar_verified": False, "max_dates_per_cycle": 1, "max_source_calls_per_date": 3,
        "message": "最近7个已到采集窗口的自然日逐日核验来源交易日历；每轮最多1日，已记录日期不自动重试，失败需受控恢复。窗口外历史不自动下载。"}


class EodSourceError(ValueError):
    def __init__(self, message: str, *, code: str = "eod_source_or_validation_failed"):
        super().__init__(message)
        self.code = code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else "eod_source_or_validation_failed"


@dataclass(frozen=True)
class EodResponse:
    data: dict
    rows: list[dict]
    evidence: dict


@dataclass(frozen=True)
class EodFetchResult:
    catalog: list[dict]
    rows: list[dict]
    has_more: bool
    source_evidence: dict


def _unique_json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON key")
        result[name] = value
    return result


def _reject_json_constant(value):
    raise ValueError("non-finite JSON constant")


def _source_rows(data, required_fields: str, *, row_limit: int = 10000) -> list[dict]:
    if not isinstance(data, dict) or data.get("has_more") is not False:
        raise EodSourceError("来源未确认完整分页")
    columns, rows = data.get("fields"), data.get("items")
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 64
            or any(not isinstance(column, str) or not column or len(column) > 64 for column in columns)
            or len(columns) != len(set(columns)) or not set(required_fields.split(",")) <= set(columns)
            or not isinstance(rows, list) or len(rows) > row_limit
            or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)):
        raise EodSourceError("来源字段结构无效")
    return [dict(zip(columns, row)) for row in rows]


class TeajoinEodSource:
    def __init__(self, token: str):
        if not token:
            raise EodSourceError("数据账号尚未配置")
        self.token = token

    async def _query(self, client, name: str, params: dict, fields: str, *, row_limit: int = 10000) -> EodResponse:
        if name not in ("trade_cal", "stock_basic", "daily"):
            raise ValueError("来源接口不在盘后白名单")
        try:
            async with client.stream("POST", f"https://teajoin.com/debug/api/{name}", json={"api_name": name, "token": self.token, "params": params, "fields": fields}) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > 8 * 1024 * 1024:
                        raise EodSourceError("盘后来源超过8MiB响应上限")
                    content.extend(chunk)
            received_at = datetime.now(timezone.utc).isoformat()
            body = json.loads(content, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
            if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0:
                raise EodSourceError("来源拒绝请求或状态码无效")
            data = body.get("data")
            rows = _source_rows(data, fields, row_limit=row_limit)
            return EodResponse(data, rows, {"api_name": name, "payload_sha256": hashlib.sha256(content).hexdigest(),
                "byte_count": len(content), "received_at": received_at, "row_count": len(rows)})
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            code = {401: "eod_source_unauthorized", 402: "eod_source_payment_required", 403: "eod_source_unauthorized", 429: "eod_source_rate_limited"}.get(status, "eod_source_unavailable" if status >= 500 else "eod_source_http_failed")
            raise EodSourceError("盘后来源HTTP失败；未自动重试，请核对任务错误类型", code=code) from None
        except EodSourceError:
            raise
        except httpx.HTTPError:
            raise EodSourceError("盘后来源连接或超时失败；未自动重试", code="eod_source_transport_failed") from None
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise EodSourceError("盘后来源结构或解析失败；未自动重试", code="eod_source_invalid_response") from None

    async def sample_daily(self, identifiers: list[str], trading_date: date) -> EodResponse:
        if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 3
                or any(not isinstance(identifier, str) or not re.fullmatch(r"(?:6\d{5}\.SH|[03]\d{5}\.SZ|[489]\d{5}\.BJ)", identifier) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers) or type(trading_date) is not date
                or trading_date > datetime.now(ZoneInfo("Asia/Shanghai")).date()):
            raise ValueError("核对最多3个不同标准A股标识及一个非未来交易日")
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            return await self._query(client, "daily", {"ts_code": ",".join(identifiers), "trade_date": trading_date.strftime("%Y%m%d"), "limit": 4, "offset": 0},
                "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount", row_limit=3)

    async def fetch(self, trading_date: date, cached_daily: dict | None = None):
        calls = 0
        evidence = {"version": "teajoin-eod-response-v1", "mode": "live_http" if cached_daily is None else "cached_daily", "responses": []}
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            async def query(name, params, fields):
                nonlocal calls
                calls += 1
                if calls > 3:
                    raise EodSourceError("盘后来源请求超出3次预算")
                result = await self._query(client, name, params, fields)
                evidence["responses"].append(result.evidence)
                return result.data, result.rows
            _, calendar = await query("trade_cal", {"exchange": "SSE", "start_date": trading_date.strftime("%Y%m%d"), "end_date": trading_date.strftime("%Y%m%d")}, "exchange,cal_date,is_open")
            if (len(calendar) != 1 or calendar[0].get("cal_date") != trading_date.strftime("%Y%m%d")
                    or calendar[0].get("exchange") != "SSE" or type(calendar[0].get("is_open")) not in (int, str)
                    or calendar[0]["is_open"] not in (0, 1, "0", "1")):
                raise EodSourceError("交易日历缺失或冲突，未猜测交易日")
            if int(calendar[0]["is_open"]) == 0:
                return None
            _, instruments = await query("stock_basic", {"list_status": "L", "limit": 6000, "offset": 0}, "ts_code,symbol,name,industry,exchange,list_date,list_status")
            catalog = normalize_stock_catalog(instruments, today=datetime.now(ZoneInfo("Asia/Shanghai")).date())
            if cached_daily is None:
                data, rows = await query("daily", {"trade_date": trading_date.strftime("%Y%m%d"), "limit": 6000, "offset": 0}, "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")
            else:
                data = cached_daily
                rows = _source_rows(data, "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount")
                evidence["canonical_cached_data_sha256"] = content_hash(data)
            return EodFetchResult(catalog, rows, data.get("has_more"), evidence)


async def claim_eod(session, trading_date: date) -> StockEodBatch | None:
    if session.get_bind().dialect.name == "postgresql":
        lock_id = int.from_bytes(hashlib.sha256(f"stock-eod:{trading_date}".encode()).digest()[:8], signed=True)
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    existing = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == trading_date).with_for_update())
    now = datetime.now(timezone.utc)
    if existing is not None:
        deadline = existing.deadline_at.replace(tzinfo=timezone.utc) if existing.deadline_at.tzinfo is None else existing.deadline_at
        if existing.status == "running" and deadline <= now:
            existing.status = existing.step = "failed"
            existing.finished_at, existing.error_code = now, "eod_execution_expired"
            existing.error_message = "上次同步未正常结束，已过期；未自动重试来源请求"
            await session.flush()
        return None
    job = StockEodBatch(trade_date=trading_date, status="running", step="fetching", request_id=uuid4().hex,
        started_at=now, deadline_at=now + timedelta(seconds=120), attempts=1, method_version=METHOD_VERSION, snapshot_json={})
    session.add(job)
    await session.flush()
    return job


async def run_eod(session_factory, source, trading_date: date, *, cached_daily: dict | None = None) -> dict:
    local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = local_now.date()
    if type(trading_date) is not date or trading_date > today:
        raise ValueError("盘后同步日期无效或在未来")
    if trading_date == today and (local_now.hour, local_now.minute) < (16, 30):
        raise ValueError("当日盘后同步仅在北京时间16:30后执行，避免把未收盘数据当作完整日线")
    async with asyncio.timeout(10), session_factory() as session:
        job = await claim_eod(session, trading_date)
        await session.commit()
        if job is None:
            return {"status": "already_recorded", "trade_date": trading_date.isoformat(), "source_calls": 0}
        job_id, request_id = job.id, job.request_id
    logger.info("request_id=%s job_id=%s operation=eod_started date=%s attempts=1", request_id, job_id, trading_date)
    try:
        async with asyncio.timeout(90):
            fetched = await source.fetch(trading_date, cached_daily)
        if fetched is None:
            snapshot, catalog = None, []
        else:
            if isinstance(fetched, EodFetchResult):
                catalog, rows, has_more = fetched.catalog, fetched.rows, fetched.has_more
                source_evidence = fetched.source_evidence
            else:
                catalog, rows, has_more = fetched
                source_evidence = None
            snapshot = normalize_eod(rows, catalog, trading_date, acquired_at=datetime.now(timezone.utc), has_more=has_more, source_evidence=source_evidence)
        async with asyncio.timeout(15), session_factory() as session:
            stored = await session.scalar(select(StockEodBatch).where(StockEodBatch.id == job_id).with_for_update())
            now = datetime.now(timezone.utc)
            deadline = stored.deadline_at.replace(tzinfo=timezone.utc) if stored.deadline_at.tzinfo is None else stored.deadline_at
            if stored.status != "running" or deadline <= now:
                raise EodSourceError("同步已过期，迟到数据未提交")
            if snapshot is not None:
                await upsert_stock_catalog(session, catalog)
                stored.snapshot_json, stored.data_version = snapshot, snapshot["data_version"]
                stored.status = "partially_completed" if snapshot["missing_codes"] or snapshot["unknown_codes"] else "completed"
            else:
                stored.status = "no_data"
                stored.error_code, stored.error_message = "market_closed", "供应商交易日历确认休市，没有创建零价行情"
            stored.step, stored.finished_at = stored.status, now
            await session.commit()
            status = stored.status
        logger.info("request_id=%s job_id=%s operation=eod_finished status=%s model_calls=0", request_id, job_id, status)
        return {"status": status, "job_id": str(job_id), "trade_date": trading_date.isoformat(), "data_version": snapshot["data_version"] if snapshot else None}
    except BaseException as error:
        try:
            async with asyncio.timeout(10), session_factory() as session:
                stored = await session.scalar(select(StockEodBatch).where(StockEodBatch.id == job_id).with_for_update())
                if stored is not None and stored.status == "running":
                    stored.status = stored.step = "failed"
                    stored.finished_at = datetime.now(timezone.utc)
                    stored.error_code = "eod_cancelled" if isinstance(error, asyncio.CancelledError) else error.code if isinstance(error, EodSourceError) else "eod_source_or_validation_failed"
                    stored.error_message = "同步已停止或数据未通过校验；未重试来源、未覆盖有效历史快照"
                    await session.commit()
        except Exception as persistence_error:
            logger.error("request_id=%s job_id=%s operation=eod_failure_save error_type=%s", request_id, job_id, type(persistence_error).__name__)
        if isinstance(error, asyncio.CancelledError):
            raise
        code = error.code if isinstance(error, EodSourceError) else "eod_source_or_validation_failed"
        logger.error("request_id=%s job_id=%s operation=eod_failed error_type=%s error_code=%s", request_id, job_id, type(error).__name__, code)
        raise EodSourceError("盘后同步失败，请凭任务编号核查；失败数据未提交", code=code) from None


class StockEodWorker:
    def __init__(self, session_factory, source, *, catchup_enabled: bool = False):
        self.session_factory, self.source = session_factory, source
        self.catchup_enabled = catchup_enabled
        self.task = None

    def start(self):
        self.task = asyncio.create_task(self.run(), name="stock-eod-worker")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def run(self):
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("operation=eod_schedule error_type=%s", type(error).__name__)
            await asyncio.sleep(300)

    async def run_once(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        dates = catchup_dates(now)
        if not self.catchup_enabled:
            local = now.astimezone(ZoneInfo("Asia/Shanghai"))
            if dates[0] != local.date():
                return {"status": "before_window", "source_calls": 0}
            return await run_eod(self.session_factory, self.source, local.date())
        async with asyncio.timeout(10), self.session_factory() as session:
            state = await read_catchup(session, now)
        candidates = sorted(set(state["pending_dates"] + state["expired_dates"]), reverse=True)
        if not candidates:
            return {"status": "caught_up", "source_calls": 0}
        return await run_eod(self.session_factory, self.source, date.fromisoformat(candidates[0]))
