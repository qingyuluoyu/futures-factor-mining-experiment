from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.stock_eod import METHOD_VERSION, content_hash, normalize_eod


def verified_quotes(snapshot: dict) -> dict:
    canonical = {key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")}
    if snapshot.get("method_version") != METHOD_VERSION or snapshot.get("data_version") != content_hash(canonical):
        raise ValueError("盘后快照内容或方法校验失败")
    unknown = set(snapshot["unknown_codes"])
    return {quote["instrument_id"]: quote for quote in snapshot["quotes"] if quote["code"] not in unknown}


def select_sample(snapshot: dict) -> list[str]:
    quotes = verified_quotes(snapshot)
    selected = []
    for suffix, preferred in ((".SH", "600519.SH"), (".SZ", "000001.SZ"), (".BJ", None)):
        candidates = sorted(identifier for identifier in quotes if identifier.endswith(suffix))
        if candidates:
            selected.append(preferred if preferred in candidates else candidates[0])
    if not selected:
        raise ValueError("有效快照没有可核对的已匹配证券")
    return selected


def compare_sample(snapshot: dict, rows: list[dict], identifiers: list[str]) -> dict:
    stored = verified_quotes(snapshot)
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 3
            or len(set(identifiers)) != len(identifiers) or any(identifier not in stored for identifier in identifiers)):
        raise ValueError("核对证券必须是快照内最多3个不同的有效标识")
    trading_date = date.fromisoformat(snapshot["trade_date"])
    catalog = [{"code": identifier.split(".")[0], "name": identifier, "exchange": {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[identifier.split(".")[1]], "list_date": None} for identifier in identifiers]
    observed = {}
    if rows:
        normalized = normalize_eod(rows, catalog, trading_date, acquired_at=datetime.now(timezone.utc), has_more=False)
        if normalized["unknown_codes"]:
            raise ValueError("来源返回未请求的证券，未将其当核对成功")
        observed = {quote["instrument_id"]: quote for quote in normalized["quotes"]}
    differences, matched = [], 0
    for identifier in identifiers:
        if identifier not in observed:
            differences.append({"instrument_id": identifier, "field": "missing_from_source"})
            continue
        changes = [{"instrument_id": identifier, "field": field, "stored": stored[identifier][field], "observed": observed[identifier][field]}
            for field in ("open", "high", "low", "close", "pre_close", "volume", "amount", "change_pct")
            if Decimal(str(stored[identifier][field])) != Decimal(str(observed[identifier][field]))]
        differences.extend(changes)
        if not changes:
            matched += 1
    return {"audit_version": "stock-eod-sample-audit-v1", "status": "no_data" if not rows else "different" if differences else "matched",
        "independent_verification": False, "verification_scope": "same_provider_sample_consistency_only",
        "trade_date": trading_date.isoformat(), "stored_data_version": snapshot["data_version"], "sample_identifiers": identifiers,
        "price_adjustment": snapshot["price_adjustment"], "amount_unit": snapshot["amount_unit"], "volume_unit": snapshot["volume_unit"],
        "stored_raw_response_evidence": bool(snapshot.get("source_evidence")), "matched_count": matched, "returned_count": len(rows), "differences": differences}
