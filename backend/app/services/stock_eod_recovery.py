from datetime import date, datetime, time, timezone
from decimal import Decimal
import json
import logging
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.models.stock_eod_recovery import StockEodRecovery
from app.services.stock_eod import METHOD_VERSION as EOD_METHOD_VERSION, content_hash, normalize_eod


METHOD_VERSION = "stock-eod-cached-recovery-v1"
logger = logging.getLogger("qihuo.stock_eod_recovery")


def validate_recovery_snapshot(snapshot: dict, catalog: list[dict]) -> dict:
    try:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("quotes"), list) or not 1 <= len(snapshot["quotes"]) <= 10000:
            raise ValueError("Recovery quote budget invalid")
        trading_date = date.fromisoformat(snapshot["trade_date"])
        collected = datetime.fromisoformat(snapshot["collected_at"])
        earliest = datetime.combine(trading_date, time(16, 30), ZoneInfo("Asia/Shanghai"))
        if collected.tzinfo is None or not earliest <= collected <= datetime.now(timezone.utc):
            raise ValueError("Recovery collection time invalid")
        evidence = snapshot["source_evidence"]
        responses = evidence["responses"]
        if evidence["mode"] != "live_http" or evidence["version"] != "teajoin-eod-response-v1" or [item["api_name"] for item in responses] != ["trade_cal", "stock_basic", "daily"]:
            raise ValueError("Recovery requires original bounded source evidence")
        for item in responses:
            stamp = datetime.fromisoformat(item["received_at"])
            if (not re.fullmatch(r"[a-f0-9]{64}", item["payload_sha256"]) or type(item["byte_count"]) is not int or not 1 <= item["byte_count"] <= 8 * 1024 * 1024
                    or type(item["row_count"]) is not int or not 0 <= item["row_count"] <= 10000 or stamp.tzinfo is None or not earliest <= stamp <= collected):
                raise ValueError("Recovery source evidence invalid")
        if responses[-1]["row_count"] != len(snapshot["quotes"]):
            raise ValueError("Recovery source rows disagree")
        rows = [{"ts_code": quote["instrument_id"], "trade_date": trading_date.strftime("%Y%m%d"),
                 **{field: quote[field] for field in ("open", "high", "low", "close", "pre_close")},
                 "vol": quote["volume"], "amount": float(Decimal(str(quote["amount"])) / 1000)} for quote in snapshot["quotes"]]
        rebuilt = normalize_eod(rows, catalog, trading_date, acquired_at=collected, has_more=False, source_evidence=evidence)
        canonical = {key: value for key, value in snapshot.items() if key not in {"data_version", "collected_at"}}
        if rebuilt["data_version"] != snapshot["data_version"] or content_hash(canonical) != snapshot["data_version"]:
            raise ValueError("Recovery snapshot or catalog version disagrees")
        return rebuilt
    except (KeyError, TypeError, ArithmeticError) as error:
        raise ValueError("Recovery snapshot structure invalid") from None


async def inspect_failed_eod(session, snapshot: dict, *, lock: bool = False):
    try:
        trading_date = date.fromisoformat(snapshot["trade_date"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Recovery trading date invalid") from None
    query = select(StockEodBatch).where(StockEodBatch.trade_date == trading_date)
    if lock:
        query = query.with_for_update()
    batch = await session.scalar(query.execution_options(populate_existing=True))
    if batch is None or batch.status != "failed" or batch.data_version is not None or batch.snapshot_json != {} or batch.method_version != EOD_METHOD_VERSION:
        raise ValueError("Only an empty failed EOD batch may be recovered")
    rows = (await session.execute(select(StockInstrument.code, StockInstrument.name, StockInstrument.exchange, StockInstrument.industry, StockInstrument.list_date).order_by(StockInstrument.code).limit(10001))).mappings().all()
    rebuilt = validate_recovery_snapshot(snapshot, [dict(row) for row in rows])
    return batch, rebuilt


async def recover_failed_eod(session, *, snapshot: dict, idempotency_key: UUID, operator_ref: str, reason: str) -> dict:
    if not isinstance(idempotency_key, UUID) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,64}", operator_ref) or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 160:
        raise ValueError("Recovery operator, reason or idempotency invalid")
    request_hash = content_hash({"snapshot_hash": content_hash(snapshot), "operator_ref": operator_ref, "reason": reason.strip()})
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET LOCAL statement_timeout = '15s'"))
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(20261020007)"))
    existing = await session.scalar(select(StockEodRecovery).where(StockEodRecovery.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ValueError("Recovery idempotency conflict")
        return {"recovery_id": str(existing.id), "data_version": existing.source_data_version, "status": "already_applied", "replayed": True, "source_calls": 0}
    batch, rebuilt = await inspect_failed_eod(session, snapshot, lock=True)
    if await session.scalar(select(StockEodRecovery.id).where(StockEodRecovery.source_batch_id == batch.id)):
        raise ValueError("This batch already has a recovery event; use audited forward repair")
    moment = datetime.now(timezone.utc)
    recovery_id = uuid4()
    before = json.loads(json.dumps({column.name: getattr(batch, column.name) for column in StockEodBatch.__table__.columns}, default=str, allow_nan=False))
    session.add(StockEodRecovery(id=recovery_id, source_batch_id=batch.id, idempotency_key=idempotency_key, request_hash=request_hash, request_id=uuid4().hex,
        operator_ref=operator_ref, reason=reason.strip(), method_version=METHOD_VERSION, source_data_version=rebuilt["data_version"], before_row=before, applied_at=moment, source_calls=0))
    batch.snapshot_json, batch.data_version = rebuilt, rebuilt["data_version"]
    batch.status = batch.step = "partially_completed" if rebuilt["missing_codes"] or rebuilt["unknown_codes"] else "completed"
    batch.finished_at, batch.error_code = moment, "eod_recovered"
    batch.error_message = "原失败与原采集尝试已留存恢复事件 " + str(recovery_id)
    await session.flush()
    logger.info("request_id=%s operation=eod_cached_recovery batch_id=%s recovery_id=%s status=%s source_calls=0", batch.request_id, batch.id, recovery_id, batch.status)
    return {"recovery_id": str(recovery_id), "data_version": rebuilt["data_version"], "status": batch.status, "replayed": False, "source_calls": 0}
