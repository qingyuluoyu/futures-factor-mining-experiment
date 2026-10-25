from datetime import date, datetime
import re

from sqlalchemy import select

from app.models.stock import StockInstrument
from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import FINANCIAL_METADATA, MAX_ROWS, prepare_archive
from app.services.stock_eod import content_hash
from app.services.stock_eod_gaps import validate_gap_snapshot
from app.services.stock_raw_panel import timestamp


METHOD_VERSION = "stock-eod-reconciliation-v1"
SUFFIXES = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}


def _versions(previous: str, current: str) -> None:
    if (any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value) for value in (previous, current))
            or previous == current):
        raise ValueError("必须指定两个不同的固定盘后SHA256版本")


def _catalog(rows: list[dict]) -> dict:
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("当前目录结构无效或超过10000条边界")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"code", "name", "exchange", "industry", "list_date"}:
            raise ValueError("当前目录字段不符合契约")
        code, exchange, listed = row["code"], row["exchange"], row["list_date"]
        if (not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or code in indexed
                or not isinstance(exchange, str) or exchange not in SUFFIXES or (listed is not None and type(listed) is not date)
                or not isinstance(row["name"], str) or not row["name"].strip() or len(row["name"]) > 32
                or not isinstance(row["industry"], str) or len(row["industry"]) > 32):
            raise ValueError("当前目录标识、日期或名称无效")
        suffix = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ" if code.startswith(("4", "8", "9")) else None
        if suffix != SUFFIXES[exchange]:
            raise ValueError("当前目录证券与交易所冲突")
        indexed[code] = dict(row)
    return indexed


def _summary(batch, indexed: dict, observed_at: datetime) -> dict:
    try:
        validate_gap_snapshot(batch.snapshot_json, batch.trade_date, batch.data_version)
        prepare_archive(batch, now=observed_at)
    except (KeyError, TypeError, OverflowError, ValueError):
        raise ValueError("固定快照元数据缺失或类型无效，未回退其他版本") from None
    snapshot = batch.snapshot_json
    projected = [indexed[code] for code in sorted(indexed) if indexed[code]["list_date"] is None or indexed[code]["list_date"] <= batch.trade_date]
    current_hash = content_hash(projected)
    return {"batch_id": str(batch.id), "data_version": batch.data_version, "trade_date": batch.trade_date.isoformat(),
            "status": batch.status, "collected_at": snapshot["collected_at"], "source_row_count": snapshot["source_row_count"],
            "expected_count": snapshot["expected_count"], "covered_count": snapshot["covered_count"],
            "missing_count": len(snapshot["missing_codes"]), "unknown_count": len(snapshot["unknown_codes"]),
            "original_catalog_version": snapshot["catalog_version"], "current_catalog_projected_version": current_hash,
            "current_catalog_hash_matches": current_hash == snapshot["catalog_version"],
            "source_evidence_recorded": isinstance(snapshot.get("source_evidence"), dict)}


def _membership(states: dict, code: str, quotes: dict) -> dict:
    return {"state": states.get(code, "outside_snapshot"), "instrument_id": quotes.get(code)}


def build_reconciliation(previous, current, catalog: list[dict], *, now: datetime) -> dict:
    observed_at = timestamp(now)
    _versions(previous.data_version, current.data_version)
    if type(previous.trade_date) is not date or type(current.trade_date) is not date or previous.trade_date >= current.trade_date:
        raise ValueError("固定版本必须按不同交易日从旧到新排列")
    indexed = _catalog(catalog)
    summaries = {"previous": _summary(previous, indexed, observed_at), "current": _summary(current, indexed, observed_at)}
    before, after = previous.snapshot_json, current.snapshot_json
    gap_sets, affected = {}, set()
    for kind in ("missing", "unknown"):
        old, new = set(before[f"{kind}_codes"]), set(after[f"{kind}_codes"])
        gap_sets[kind] = {"persistent": sorted(old & new), "appeared": sorted(new - old), "no_longer_flagged": sorted(old - new)}
        affected.update(old | new)
    quote_maps = [{quote["code"]: quote["instrument_id"] for quote in snapshot["quotes"]} for snapshot in (before, after)]
    states = []
    for snapshot, quotes in zip((before, after), quote_maps, strict=True):
        membership = dict.fromkeys(quotes, "matched_quote")
        membership.update(dict.fromkeys(snapshot["unknown_codes"], "unknown"))
        membership.update(dict.fromkeys(snapshot["missing_codes"], "missing"))
        states.append(membership)
    items = []
    for code in sorted(affected):
        row = indexed.get(code)
        identifier = code + "." + SUFFIXES[row["exchange"]] if row else None
        source_id = quote_maps[1].get(code) or quote_maps[0].get(code)
        status = ("absent_now" if row is None else "exchange_conflict_now" if source_id and source_id != identifier
                  else "listed_after_snapshot_now" if row["list_date"] and row["list_date"] > current.trade_date else "eligible_now")
        items.append({"code": code, "before": _membership(states[0], code, quote_maps[0]), "after": _membership(states[1], code, quote_maps[1]),
                      "current_catalog": {"status": status, "instrument_id": identifier, "basis": "current_catalog_not_original_membership",
                                          "name": row["name"] if row else None, "industry": row["industry"] if row else None,
                                          "list_date": row["list_date"].isoformat() if row and row["list_date"] else None},
                      "cause": "unverified_requires_authorized_source_evidence"})
    result = {"method_version": METHOD_VERSION, "scope": "two_pinned_stored_snapshots_and_current_catalog_annotations",
              "batches": summaries, "financial_metadata": dict(FINANCIAL_METADATA), "gap_sets": gap_sets, "items": items,
              "item_count": len(items), "missing_disappearance_is_not_repair": True,
              "independent_verification": False, "point_in_time_verified": False, "eligible_for_backtest": False,
              "calendar_continuity_verified": False, "source_calls": 0, "model_calls": 0}
    result["content_sha256"] = content_hash(result)
    result["generated_at"] = observed_at.isoformat()
    return result


async def read_reconciliation(session, previous: str, current: str, *, now: datetime) -> dict:
    _versions(previous, current)
    batches = (await session.scalars(select(StockEodBatch).where(StockEodBatch.data_version.in_((previous, current))).limit(3))).all()
    if len(batches) != 2 or {batch.data_version for batch in batches} != {previous, current}:
        raise ValueError("固定盘后版本缺失或重复，未回退最新批次")
    indexed = {batch.data_version: batch for batch in batches}
    columns = (StockInstrument.code, StockInstrument.name, StockInstrument.exchange, StockInstrument.industry, StockInstrument.list_date)
    rows = (await session.execute(select(*columns).order_by(StockInstrument.code).limit(MAX_ROWS + 1))).mappings().all()
    return build_reconciliation(indexed[previous], indexed[current], [dict(row) for row in rows], now=now)
