import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import logging
import re
import threading
from uuid import UUID

from sqlalchemy import select

from app.models.stock_daily_archive import StockDailyArchiveBatch, StockDailyObservation
from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import FINANCIAL_METADATA, MAX_ROWS, prepare_archive
from app.services.stock_eod import content_hash


MAX_VERSIONS = 10
MAX_INSTRUMENTS = 10
NUMBERS = ("open", "high", "low", "close", "pre_close", "volume", "amount")
logger = logging.getLogger("qihuo.stock_raw_panel")
raw_panel_cpu_gate = threading.BoundedSemaphore(2)


class RawPanelVersionNotFound(ValueError):
    pass


class RawPanelBusy(ValueError):
    pass


def guarded_build_raw_panel(records, versions, instrument_ids, *, now):
    if not raw_panel_cpu_gate.acquire(blocking=False):
        raise RawPanelBusy("归档内容校验线程已满，未排队或重复执行")
    try:
        return build_raw_panel(records, versions, instrument_ids, now=now)
    finally:
        raw_panel_cpu_gate.release()


def selection(versions: list[str], instrument_ids: list[str]) -> tuple[list[str], list[str]]:
    if (not isinstance(versions, list) or not 1 <= len(versions) <= MAX_VERSIONS
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in versions)
            or len(set(versions)) != len(versions)):
        raise ValueError("必须指定1至10个不同归档SHA256版本")
    if (not isinstance(instrument_ids, list) or not 1 <= len(instrument_ids) <= MAX_INSTRUMENTS
            or any(not isinstance(value, str) or not re.fullmatch(r"(?:6[0-9]{5}\.SH|[03][0-9]{5}\.SZ|[489][0-9]{5}\.BJ)", value) for value in instrument_ids)
            or len(set(instrument_ids)) != len(instrument_ids)):
        raise ValueError("必须指定1至10个不同标准A股证券标识，不接受裸代码")
    return sorted(versions), sorted(instrument_ids)


def timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("原价审计时间必须包含时区")
    return value.astimezone(timezone.utc)


def decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("归档观察必须为有限Decimal，不允许浮点替代")
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def build_raw_panel(records: list, versions: list[str], instrument_ids: list[str], *, now: datetime) -> dict:
    pinned, instruments = selection(versions, instrument_ids)
    observed_at = timestamp(now)
    if not isinstance(records, list) or len(records) != len(pinned):
        raise ValueError("指定归档版本不存在或其来源不可读取，未返回空面板")
    found, dates, batches, observations, missing = set(), set(), [], [], []
    for source, batch, stored in records:
        if (not isinstance(batch.id, UUID) or batch.version not in pinned or batch.version in found
                or timestamp(batch.archived_at) > observed_at):
            raise ValueError("归档版本、标识或归档时间不一致")
        prepared = prepare_archive(source, now=batch.archived_at)
        expected = prepared["batch"]
        fields = ("version", "source_batch_id", "source_data_version", "method_version", "trade_date", "status",
            "source_collected_at", "row_count", "expected_count", "missing_count", "unknown_count", "source_metadata")
        if any(getattr(batch, name) != expected[name] for name in fields):
            raise ValueError("归档元数据与已存来源不一致，未升级可信资格")
        if batch.trade_date in dates:
            raise ValueError("同一交易日指定多个版本，不允许混合基准")
        if not isinstance(stored, list) or len(stored) != expected["row_count"]:
            raise ValueError("归档全量观察数量不一致")
        wanted = [{**item, "batch_id": batch.id} for item in prepared["rows"]]
        for item in stored:
            if not isinstance(item, dict) or not isinstance(item.get("instrument_id"), str):
                raise ValueError("归档观察结构无效")
            for name in NUMBERS:
                decimal_text(item.get(name))
        if sorted(stored, key=lambda item: item["instrument_id"]) != wanted:
            raise ValueError("归档全量观察与已存来源内容不一致")
        found.add(batch.version)
        dates.add(batch.trade_date)
        batches.append({"version": batch.version, "source_data_version": batch.source_data_version,
            "source_batch_id": str(batch.source_batch_id), "archive_batch_id": str(batch.id),
            "trade_date": batch.trade_date.isoformat(), "method_version": batch.method_version,
            "source_collected_at": timestamp(batch.source_collected_at).isoformat(),
            "archived_at": timestamp(batch.archived_at).isoformat(), "verified_row_count": len(stored),
            "expected_count": batch.expected_count, "missing_count": batch.missing_count, "unknown_count": batch.unknown_count})
        selected = {item["instrument_id"]: item for item in stored if item["instrument_id"] in instruments}
        source_missing = set(source.snapshot_json["missing_codes"])
        source_unknown = set(source.snapshot_json["unknown_codes"])
        for instrument in instruments:
            common = {"instrument_id": instrument, "trade_date": batch.trade_date.isoformat(), "archive_version": batch.version}
            item = selected.get(instrument)
            if item is None:
                code = instrument.split(".")[0]
                reason = "source_missing" if code in source_missing else "source_unknown_quarantined" if code in source_unknown else "outside_snapshot"
                missing.append({**common, "reason": reason})
            else:
                observations.append({**common, **{name: decimal_text(item[name]) for name in NUMBERS}})
    if found != set(pinned):
        raise ValueError("固定版本清单未完整读取")
    result = {"manifest_version": "stock-raw-panel-v1", "versions": pinned, "instrument_ids": instruments,
        "financial_metadata": dict(FINANCIAL_METADATA), "batches": sorted(batches, key=lambda item: item["trade_date"]),
        "observations": sorted(observations, key=lambda item: (item["trade_date"], item["instrument_id"])),
        "missing": sorted(missing, key=lambda item: (item["trade_date"], item["instrument_id"])),
        "verification": "stored_source_and_archive_content_equal_not_independently_certified",
        "number_encoding": "decimal_of_stored_json_number_not_original_response_text", "data_available_at": None,
        "point_in_time_verified": False, "eligible_for_backtest": False,
        "usage": "internal_raw_observation_audit_not_backtest_dataset", "calendar_continuity_verified": False,
        "source_calls": 0, "model_calls": 0}
    result["content_sha256"] = content_hash(result)
    result["generated_at"] = observed_at.isoformat()
    return result


async def read_raw_panel(session, versions: list[str], instrument_ids: list[str], *, now: datetime) -> dict:
    pinned, instruments = selection(versions, instrument_ids)
    batches = (await session.scalars(select(StockDailyArchiveBatch).where(StockDailyArchiveBatch.version.in_(pinned)).limit(MAX_VERSIONS + 1))).all()
    if len(batches) != len(pinned):
        raise RawPanelVersionNotFound("指定归档版本不存在，未自动改读最新版本")
    sources = (await session.scalars(select(StockEodBatch).where(StockEodBatch.id.in_([batch.source_batch_id for batch in batches])).limit(MAX_VERSIONS + 1))).all()
    source_by_id = {source.id: source for source in sources}
    if any(batch.source_batch_id not in source_by_id for batch in batches):
        raise ValueError("归档来源批次缺失，未仅凭影子表放行")
    rows = (await session.execute(select(*StockDailyObservation.__table__.columns).where(
        StockDailyObservation.batch_id.in_([batch.id for batch in batches])).order_by(
        StockDailyObservation.batch_id, StockDailyObservation.instrument_id).limit(MAX_ROWS * len(batches) + 1))).mappings().all()
    if len(rows) > MAX_ROWS * len(batches):
        raise ValueError("归档全量观察超出校验边界")
    grouped = {batch.id: [] for batch in batches}
    for item in rows:
        grouped[item["batch_id"]].append(dict(item))
    result = await asyncio.to_thread(guarded_build_raw_panel, [(source_by_id[batch.source_batch_id], batch, grouped[batch.id]) for batch in batches], pinned, instruments, now=now)
    logger.info("operation=stock_raw_panel versions=%s instruments=%s verified_rows=%s output_rows=%s source_calls=0 model_calls=0",
        len(pinned), len(instruments), len(rows), len(result["observations"]))
    return result
