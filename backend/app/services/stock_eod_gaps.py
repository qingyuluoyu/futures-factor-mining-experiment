import re
import math
from datetime import date, datetime

from sqlalchemy import func, select

from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument
from app.schemas.stock_eod_gaps import EodGapQuery
from app.services.stock_eod import METHOD_VERSION, SOURCE_URI, content_hash, latest_eod
from app.services.stock_market import valid_bar


class GapSnapshotChanged(ValueError):
    pass


def validate_gap_snapshot(snapshot: dict, trade_date: date, version: str) -> None:
    if not isinstance(snapshot, dict) or not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", version):
        raise ValueError("快照版本缺失")
    canonical = {key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")}
    required = {"data_version": version, "method_version": METHOD_VERSION, "trade_date": trade_date.isoformat(), "market": "CN_A", "currency": "CNY", "timezone": "Asia/Shanghai", "frequency": "daily", "price_adjustment": "none", "volume_unit": "lot_100_shares", "source_uri": SOURCE_URI, "vendor_has_more": False}
    if any(snapshot.get(key) != value for key, value in required.items()) or snapshot.get("vendor_has_more") is not False or content_hash(canonical) != version:
        raise ValueError("快照内容或口径冲突")
    collected = datetime.fromisoformat(snapshot["collected_at"])
    if collected.tzinfo is None or collected.utcoffset() is None:
        raise ValueError("采集时点缺少时区")
    if snapshot.get("provider") != "user_authorized_teajoin_tushare_compatible" or snapshot.get("license_scope") != "operator_configured_account_not_redistribution_certified":
        raise ValueError("来源元数据冲突")
    for key in ("source_row_count", "expected_count", "covered_count"):
        if type(snapshot.get(key)) is not int or not 0 <= snapshot[key] <= 10000:
            raise ValueError("快照数量无效")
    for key in ("missing_codes", "unknown_codes"):
        values = snapshot.get(key)
        if not isinstance(values, list) or len(values) > 10000 or any(not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) for code in values) or len(set(values)) != len(values):
            raise ValueError("缺口集合无效")
    quotes = snapshot.get("quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= 10000 or len(quotes) != snapshot["source_row_count"]:
        raise ValueError("行情集合数量冲突")
    quote_codes = set()
    for quote in quotes:
        if not isinstance(quote, dict):
            raise ValueError("行情结构无效")
        code, identifier = quote.get("code"), quote.get("instrument_id")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or identifier not in {code + suffix for suffix in (".SH", ".SZ", ".BJ")} or code in quote_codes or quote.get("trade_date") != trade_date.isoformat():
            raise ValueError("证券标识或日期冲突")
        if not ((identifier.endswith(".SH") and code.startswith("6")) or (identifier.endswith(".SZ") and code.startswith(("0", "3"))) or (identifier.endswith(".BJ") and code.startswith(("4", "8", "9")))):
            raise ValueError("非A股行情标识")
        if any(type(quote.get(field)) not in (int, float) or not math.isfinite(quote[field]) for field in ("open", "high", "low", "close", "volume", "pre_close", "amount", "change_pct")) or not valid_bar(quote) or quote["pre_close"] <= 0 or quote["amount"] < 0:
            raise ValueError("行情字段无效")
        quote_codes.add(code)
    missing, unknown = set(snapshot["missing_codes"]), set(snapshot["unknown_codes"])
    if missing & quote_codes or not unknown.issubset(quote_codes) or snapshot["covered_count"] != len(quote_codes - unknown) or snapshot["expected_count"] != snapshot["covered_count"] + len(missing):
        raise ValueError("快照覆盖计数或成员冲突")


def unavailable_gap_result(state: str, query: EodGapQuery) -> dict:
    if query.expected_fingerprint is not None:
        raise GapSnapshotChanged("盘后缺口来源状态已变化，请重新应用条件；未混合分页")
    return {"gap_version": "stock-eod-gaps-v1", "state": state, "snapshot": None, "totals": None, "query_fingerprint": None, "items": [], "total": 0, "has_more": False, "applied_query": query.model_dump()}


async def read_eod_gaps(session, query: EodGapQuery, *, enabled: bool, today: date) -> dict:
    if not enabled:
        return unavailable_gap_result("disabled", query)
    batch = await latest_eod(session, today)
    if batch is None:
        return unavailable_gap_result("no_snapshot", query)
    snapshot = batch.snapshot_json
    if batch.method_version != METHOD_VERSION:
        raise ValueError("批次方法版本冲突")
    validate_gap_snapshot(snapshot, batch.trade_date, batch.data_version)
    missing, unknown = set(snapshot["missing_codes"]), set(snapshot["unknown_codes"])
    codes = missing | unknown
    catalog_rows = (await session.execute(select(StockInstrument.code, StockInstrument.name, StockInstrument.industry, StockInstrument.exchange, StockInstrument.list_date).where(StockInstrument.code.in_(sorted(codes))).limit(20001))).mappings().all() if codes else []
    catalog = {row["code"]: row for row in catalog_rows}
    source_ids = {quote["code"]: quote["instrument_id"] for quote in snapshot["quotes"]}
    suffixes = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}
    rows = []
    for code in sorted(codes):
        current = catalog.get(code)
        current_id = code + suffixes[current["exchange"]] if current is not None and current["exchange"] in suffixes else None
        kind = "missing" if code in missing else "unknown"
        identifier = source_ids[code] if kind == "unknown" else current_id
        rows.append({"code": code, "kind": kind, "name": current["name"] if current else None, "industry": current["industry"] if current else None,
            "catalog_status": "absent_now" if current is None else "exchange_conflict_now" if kind == "unknown" and current_id != identifier else "present_now",
            "instrument_id": identifier, "instrument_id_basis": "source_snapshot" if kind == "unknown" else "current_catalog" if current_id else "unavailable",
            "current_list_date": current["list_date"].isoformat() if current and current["list_date"] else None})
    fingerprint = content_hash({"version": "stock-eod-gaps-v1", "snapshot_version": snapshot["data_version"], "catalog_annotations": rows, "query": query.model_dump(exclude={"limit", "offset", "expected_fingerprint"})})
    if query.expected_fingerprint is not None and query.expected_fingerprint != fingerprint:
        raise GapSnapshotChanged("盘后快照或当前目录注释已变化，请重新应用条件；未混合分页")
    keyword = query.keyword.strip().casefold()
    filtered = [row for row in rows if (query.kind == "all" or query.kind == row["kind"]) and (not keyword or keyword in row["code"] or keyword in (row["name"] or "").casefold())]
    page = filtered[query.offset:query.offset + query.limit]
    history_rows = (await session.execute(select(KlineHistory.code, func.count().label("rows"), func.min(KlineHistory.trade_date).label("first_date"), func.max(KlineHistory.trade_date).label("last_date")).where(KlineHistory.market_type == "stock", KlineHistory.code.in_([row["code"] for row in page]), KlineHistory.trade_date <= batch.trade_date).group_by(KlineHistory.code))).mappings().all() if page else []
    history = {row["code"]: {"rows": row["rows"], "first_date": row["first_date"].isoformat(), "last_date": row["last_date"].isoformat()} for row in history_rows}
    return {"gap_version": "stock-eod-gaps-v1", "state": "ready", "snapshot": {
        "trade_date": batch.trade_date.isoformat(), "data_version": snapshot["data_version"], "provider": snapshot["provider"], "source_uri": snapshot["source_uri"], "collected_at": snapshot["collected_at"],
        "price_adjustment": "none", "volume_unit": snapshot["volume_unit"], "license_scope": snapshot["license_scope"], "source_evidence_recorded": isinstance(snapshot.get("source_evidence"), dict),
        "verification": "stored_metadata_only_not_independent_certification", "history_adjustment": "legacy_qfq_unversioned"},
        "totals": {"missing": len(missing), "unknown": len(unknown)}, "query_fingerprint": fingerprint, "items": [{**row, "history": history.get(row["code"], {"rows": 0, "first_date": None, "last_date": None})} for row in page],
        "total": len(filtered), "has_more": query.offset + query.limit < len(filtered), "applied_query": query.model_dump()}
