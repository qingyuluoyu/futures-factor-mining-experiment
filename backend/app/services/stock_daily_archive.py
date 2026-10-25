from datetime import date, datetime, timezone
from decimal import Decimal
import logging
import json
import math
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.services.stock_eod import METHOD_VERSION as EOD_METHOD_VERSION, SOURCE_URI, content_hash


METHOD_VERSION = "stock-daily-archive-2026-10-19.v1"
MAX_ROWS = 10000
SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("qihuo.stock_daily_archive")
FINANCIAL_METADATA = {"market": "CN_A", "currency": "CNY", "timezone": "Asia/Shanghai", "frequency": "daily",
    "price_adjustment": "none", "volume_unit": "lot_100_shares", "amount_unit": "CNY",
    "provider": "user_authorized_teajoin_tushare_compatible", "source_uri": SOURCE_URI,
    "reference_price": "vendor_ex_rights_pre_close", "license_scope": "operator_configured_account_not_redistribution_certified"}


def _timestamp(value) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("归档时间必须包含时区")
    return value.astimezone(timezone.utc)


def _number(value, *, zero_allowed: bool) -> Decimal:
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError("归档价量必须为有限JSON数值")
    number = Decimal(str(value))
    if number < 0 or (not zero_allowed and number == 0) or number >= Decimal("1e20") or number.as_tuple().exponent < -18:
        raise ValueError("归档价量无效或超过Numeric38,18，未做截断")
    return number


def _codes(values) -> set[str]:
    if (not isinstance(values, list) or len(values) > MAX_ROWS
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9]{6}", value) for value in values)
            or len(values) != len(set(values))):
        raise ValueError("归档覆盖代码列表无效")
    return set(values)


def prepare_archive(source_batch, *, now: datetime) -> dict:
    observed_at = _timestamp(now)
    local = observed_at.astimezone(SHANGHAI)
    trading_date = source_batch.trade_date
    if (type(trading_date) is not date or trading_date > local.date()
            or (trading_date == local.date() and (local.hour, local.minute) < (16, 30))
            or source_batch.status not in ("completed", "partially_completed")
            or source_batch.finished_at is None or _timestamp(source_batch.finished_at) > observed_at
            or not isinstance(source_batch.id, UUID)):
        raise ValueError("仅归档已结束且非未来或未收盘的EOD批次")
    snapshot = source_batch.snapshot_json
    if not isinstance(snapshot, dict):
        raise ValueError("EOD快照结构无效")
    canonical = {key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")}
    if (source_batch.method_version != EOD_METHOD_VERSION or snapshot.get("method_version") != EOD_METHOD_VERSION
            or snapshot.get("data_version") != source_batch.data_version or content_hash(canonical) != source_batch.data_version
            or snapshot.get("trade_date") != trading_date.isoformat()
            or any(snapshot.get(name) != value for name, value in FINANCIAL_METADATA.items())
            or snapshot.get("vendor_has_more") is not False
            or not isinstance(snapshot.get("catalog_version"), str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot["catalog_version"])):
        raise ValueError("EOD来源、单位、复权或内容版本不匹配")
    try:
        collected_at = _timestamp(datetime.fromisoformat(snapshot["collected_at"]))
    except (KeyError, TypeError, ValueError):
        raise ValueError("快照采集时间无效") from None
    collected_local = collected_at.astimezone(SHANGHAI)
    if (collected_at > observed_at or collected_local.date() < trading_date or collected_at > _timestamp(source_batch.finished_at)
            or (collected_local.date() == trading_date and (collected_local.hour, collected_local.minute) < (16, 30))):
        raise ValueError("快照采集与归档时点冲突")
    quotes = snapshot.get("quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= MAX_ROWS:
        raise ValueError("归档观察行缺失或超过边界")
    for name in ("source_row_count", "covered_count", "expected_count"):
        if type(snapshot.get(name)) is not int or not 0 <= snapshot[name] <= MAX_ROWS:
            raise ValueError("归档覆盖数量无效")
    missing, unknown = _codes(snapshot.get("missing_codes")), _codes(snapshot.get("unknown_codes"))
    rows, seen = [], set()
    batch_id = uuid4()
    for quote in quotes:
        if not isinstance(quote, dict):
            raise ValueError("归档行结构无效")
        code, identifier = quote.get("code"), quote.get("instrument_id")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or code in seen:
            raise ValueError("归档证券代码无效或重复")
        suffix = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ" if code.startswith(("4", "8", "9")) else None
        if suffix is None or identifier != f"{code}.{suffix}" or quote.get("trade_date") != trading_date.isoformat():
            raise ValueError("归档证券标识或交易日冲突")
        values = {name: _number(quote.get(name), zero_allowed=name in ("volume", "amount"))
            for name in ("open", "high", "low", "close", "pre_close", "volume", "amount")}
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError("归档OHLC关系无效")
        seen.add(code)
        if code not in unknown:
            rows.append({"batch_id": batch_id, "instrument_id": identifier, "code": code,
                "exchange": {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[suffix], "trade_date": trading_date,
                **{name: FINANCIAL_METADATA[name] for name in ("currency", "price_adjustment", "volume_unit", "amount_unit")}, **values})
    if (len(quotes) != snapshot["source_row_count"] or len(rows) != snapshot["covered_count"]
            or len(rows) + len(missing) != snapshot["expected_count"] or not unknown <= seen
            or missing & seen or len(rows) + len(unknown) != len(quotes) or not rows
            or source_batch.status != ("partially_completed" if missing or unknown else "completed")):
        raise ValueError("归档覆盖分区或批次状态不一致")
    rows.sort(key=lambda item: item["instrument_id"])
    version = content_hash({"method_version": METHOD_VERSION, "source_data_version": source_batch.data_version})
    evidence = snapshot.get("source_evidence")
    if evidence is not None and (not isinstance(evidence, dict) or len(json.dumps(evidence, allow_nan=False).encode("utf-8")) > 32768):
        raise ValueError("归档来源证据结构无效或超过32KiB边界")
    metadata = {**FINANCIAL_METADATA, "catalog_version": snapshot["catalog_version"], "source_row_count": len(quotes),
        "verification": "snapshot_only_not_independently_certified", "data_available_at": None,
        "number_encoding": "decimal_of_stored_json_number_not_original_response_text",
        "source_evidence": evidence}
    return {"batch": {"id": batch_id, "version": version, "source_batch_id": source_batch.id,
        "source_data_version": source_batch.data_version, "method_version": METHOD_VERSION, "trade_date": trading_date,
        "status": "completed", "source_collected_at": collected_at, "archived_at": observed_at, "row_count": len(rows),
        "expected_count": snapshot["expected_count"], "missing_count": len(missing), "unknown_count": len(unknown), "source_metadata": metadata}, "rows": rows}


async def archive_eod(session, source_batch, *, now: datetime) -> dict:
    from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation

    prepared = prepare_archive(source_batch, now=now)
    batch = prepared["batch"]
    identifier = (await session.execute(insert(StockDailyArchiveBatch).values(**batch)
        .on_conflict_do_nothing().returning(StockDailyArchiveBatch.id))).scalar_one_or_none()
    if identifier is None:
        existing = (await session.execute(select(StockDailyArchiveBatch).where(
            StockDailyArchiveBatch.source_batch_id == source_batch.id, StockDailyArchiveBatch.method_version == METHOD_VERSION))).scalar_one_or_none()
        if (existing is None or existing.version != batch["version"] or existing.row_count != batch["row_count"]
                or existing.status != "completed"):
            raise ValueError("归档冲突或既有版本不一致，未覆盖")
        fields = ("source_data_version", "method_version", "trade_date", "status", "source_collected_at", "row_count",
            "expected_count", "missing_count", "unknown_count", "source_metadata")
        if any(getattr(existing, name) != batch[name] for name in fields):
            raise ValueError("既有归档元数据与来源不一致，未覆盖")
        columns = [column for column in StockDailyObservation.__table__.columns if column.name != "batch_id"]
        stored = (await session.execute(select(*columns).where(StockDailyObservation.batch_id == existing.id)
            .order_by(StockDailyObservation.instrument_id).limit(MAX_ROWS + 1))).mappings().all()
        expected = [{name: value for name, value in item.items() if name != "batch_id"} for item in prepared["rows"]]
        if [dict(item) for item in stored] != expected:
            raise ValueError("既有归档观察内容与来源不一致，未覆盖")
        result = {"status": "already_archived", "id": str(existing.id), "version": existing.version, "row_count": len(stored)}
    else:
        for offset in range(0, len(prepared["rows"]), 500):
            await session.execute(insert(StockDailyObservation), prepared["rows"][offset:offset + 500])
        result = {"status": "archived", "id": str(identifier), "version": batch["version"], "row_count": batch["row_count"]}
    logger.info("operation=stock_daily_archive source_batch_id=%s version=%s status=%s rows=%s source_calls=0 model_calls=0 commit_by_caller=true",
        source_batch.id, result["version"], result["status"], result["row_count"])
    return result
