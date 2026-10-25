import hashlib
import json
import math
import re
from datetime import date, datetime, timezone


AUDIT_VERSION = "international-metals-audit-2026-10-18.v1"
MAX_ROWS = 1000
MAX_BYTES = 2 * 1024 * 1024
PROVIDER = "user_authorized_teajoin_tushare_compatible"
SOURCE_URI = "https://teajoin.com/debug/api/fx_daily"
PRICE_FIELDS = tuple(f"{side}_{field}" for side in ("bid", "ask") for field in ("open", "high", "low", "close"))
REQUIRED_FIELDS = {"ts_code", "trade_date", "tick_qty", *PRICE_FIELDS}
ALIASES = {alias: f"{metal}USD.FXCM" for metal in ("XAU", "XAG")
    for alias in (f"{metal}USD", f"{metal}/USD", f"{metal}USD.FXCM", f"{metal}/USD.FXCM")}


def normalize_international_metal(symbol: str) -> str:
    if not isinstance(symbol, str) or symbol.strip().upper() not in ALIASES:
        raise ValueError("国际贵金属标识不在已知来源白名单")
    return ALIASES[symbol.strip().upper()]


def is_international_metal(symbol: object) -> bool:
    return isinstance(symbol, str) and symbol.strip().upper() in ALIASES


class _PayloadError(ValueError):
    pass


def _json_object(pairs: list[tuple]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate key")
        result[name] = value
    return result


def _reject_constant(value: str):
    raise ValueError("non-finite JSON constant")


def _decode(payload: bytes, required_fields: set[str]) -> list[dict]:
    if not isinstance(payload, bytes):
        raise _PayloadError("invalid_payload_type")
    if len(payload) > MAX_BYTES:
        raise _PayloadError("payload_limit_exceeded")
    try:
        envelope = json.loads(payload.decode("utf-8"), object_pairs_hook=_json_object, parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise _PayloadError("invalid_json") from None
    if not isinstance(envelope, dict) or set(envelope) - {"fields", "items", "has_more"}:
        raise _PayloadError("invalid_envelope")
    if envelope.get("has_more") is not False:
        raise _PayloadError("incomplete_pagination")
    fields, items = envelope.get("fields"), envelope.get("items")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise _PayloadError("invalid_fields")
    if len(fields) > 64 or len(set(fields)) != len(fields) or not required_fields <= set(fields):
        raise _PayloadError("invalid_fields")
    if not isinstance(items, list):
        raise _PayloadError("invalid_envelope")
    if len(items) > MAX_ROWS:
        raise _PayloadError("row_limit_exceeded")
    if any(not isinstance(item, list) or len(item) != len(fields) for item in items):
        raise _PayloadError("invalid_row_shape")
    return [dict(zip(fields, item)) for item in items]


def _positive_price(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def audit_international_metals(payload: bytes, *, symbol: str, start_date: date, end_date: date,
        as_of: datetime, catalog_payload: bytes | None = None, collected_at: datetime | None = None) -> dict:
    identifier = normalize_international_metal(symbol)
    if type(start_date) is not date or type(end_date) is not date or start_date > end_date:
        raise ValueError("审计日期范围无效")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("审计时点必须包含时区")
    if collected_at is not None and (not isinstance(collected_at, datetime) or collected_at.tzinfo is None
            or collected_at.utcoffset() is None or collected_at > as_of):
        raise ValueError("采集时点必须包含时区且不得晚于审计时点")
    issues = {}

    def issue(code: str, severity: str, row_index: int | None = None):
        entry = issues.setdefault(code, {"code": code, "severity": severity, "count": 0, "sample_row_indices": []})
        entry["count"] += 1
        if row_index is not None and len(entry["sample_row_indices"]) < 10:
            entry["sample_row_indices"].append(row_index)

    if end_date > as_of.astimezone(timezone.utc).date():
        issue("future_request_range", "reject")
    if collected_at is None:
        issue("collection_time_unverified", "certification")
    issue("session_semantics_unverified", "certification")
    issue("contract_specification_unverified", "certification")
    issue("license_scope_unverified", "certification")
    issue("bar_availability_time_unverified", "certification")
    issue("freshness_policy_unverified", "certification")
    try:
        rows = _decode(payload, REQUIRED_FIELDS)
    except _PayloadError as error:
        issue(str(error), "reject")
        rows = []
    else:
        if not rows:
            issue("no_data", "reject")
    seen, weekend_dates, observed_dates = set(), set(), []
    validated_rows = 0
    for row_index, row in enumerate(rows):
        rejected_before = sum(entry["count"] for entry in issues.values() if entry["severity"] == "reject")
        if row["ts_code"] != identifier:
            issue("security_mismatch", "reject", row_index)
        if row.get("exchange") is None:
            issue("dealer_missing", "certification", row_index)
        elif row["exchange"] != "FXCM":
            issue("dealer_mismatch", "reject", row_index)
        label = row["trade_date"]
        try:
            if not isinstance(label, str) or not re.fullmatch(r"\d{8}", label):
                raise ValueError("invalid date label")
            session_date = datetime.strptime(label, "%Y%m%d").date()
        except ValueError:
            issue("invalid_trade_date", "reject", row_index)
        else:
            observed_dates.append(session_date.isoformat())
            if not start_date <= session_date <= end_date or session_date > as_of.astimezone(timezone.utc).date():
                issue("date_out_of_range", "reject", row_index)
            if collected_at is not None and session_date > collected_at.astimezone(timezone.utc).date():
                issue("date_after_collection", "reject", row_index)
            if label in seen:
                issue("duplicate_session", "reject", row_index)
            seen.add(label)
            if session_date.weekday() >= 5:
                weekend_dates.add(session_date.isoformat())
                issue("weekend_sessions_unverified", "certification", row_index)
        if not all(_positive_price(row[field]) for field in PRICE_FIELDS):
            issue("invalid_price", "reject", row_index)
        else:
            for side in ("bid", "ask"):
                if not row[f"{side}_low"] <= min(row[f"{side}_open"], row[f"{side}_close"]) <= max(
                        row[f"{side}_open"], row[f"{side}_close"]) <= row[f"{side}_high"]:
                    issue("invalid_ohlc", "reject", row_index)
            if any(row[f"ask_{field}"] < row[f"bid_{field}"] for field in ("open", "high", "low", "close")):
                issue("crossed_quote", "reject", row_index)
        if type(row["tick_qty"]) is not int or row["tick_qty"] < 0:
            issue("invalid_tick_qty", "reject", row_index)
        rejected_after = sum(entry["count"] for entry in issues.values() if entry["severity"] == "reject")
        if rejected_after == rejected_before:
            validated_rows += 1
    observed_catalog = None
    if catalog_payload is None:
        issue("catalog_missing", "certification")
    else:
        try:
            directory = _decode(catalog_payload, {"ts_code"})
        except _PayloadError as error:
            issue(f"catalog_{error}", "reject")
        else:
            matching = [entry for entry in directory if entry["ts_code"] == identifier]
            if not directory:
                issue("catalog_empty", "certification")
            elif not matching:
                issue("catalog_security_missing", "certification")
            elif len(matching) > 1:
                issue("catalog_security_duplicate", "reject")
            else:
                contract = matching[0]
                observed_catalog = {field: contract.get(field) for field in ("classify", "exchange", "min_unit", "max_unit",
                    "pip", "pip_cost", "trading_hours", "break_time")}
                for field, value in observed_catalog.items():
                    valid = value is None
                    if field in ("classify", "exchange", "trading_hours", "break_time"):
                        valid = valid or (isinstance(value, str) and len(value) <= 256)
                    elif type(value) in (int, float):
                        try:
                            valid = math.isfinite(value)
                        except OverflowError:
                            valid = False
                    if not valid:
                        issue("catalog_field_type_invalid", "reject")
                        observed_catalog[field] = None
                if contract.get("classify") == "COMMODITY":
                    issue("catalog_classification_conflict", "certification")
                elif contract.get("classify") != "METAL":
                    issue("catalog_identity_mismatch", "reject")
                if contract.get("exchange") != "FXCM":
                    issue("catalog_identity_mismatch", "reject")
                if not all(_positive_price(contract.get(field)) for field in ("pip", "pip_cost", "min_unit", "max_unit")):
                    issue("catalog_specification_missing", "certification")
                elif contract["min_unit"] > contract["max_unit"]:
                    issue("catalog_specification_invalid", "reject")
    rejected = any(entry["severity"] == "reject" for entry in issues.values())
    return {"audit_version": AUDIT_VERSION, "instrument_id": identifier,
        "validation_status": "rejected" if rejected else "pending_certification",
        "numerical_validation": "failed" if rejected else "passed",
        "capabilities": {"market_reference": False, "simulation": False, "backtest": False},
        "source_row_count": len(rows), "validated_row_count": validated_rows,
        "observed": {"first_date": min(observed_dates, default=None), "last_date": max(observed_dates, default=None),
            "weekend_dates": sorted(weekend_dates), "catalog": observed_catalog},
        "metadata": {"provider": PROVIDER, "market": "international_metals", "dealer": "FXCM",
            "currency": "USD", "frequency": "daily", "documented_date_timezone": "GMT",
            "session_calendar": None, "session_day_boundary": None, "price_unit": None,
            "contract_multiplier": None, "price_adjustment": "unverified", "instrument_type": "unverified",
            "tick_qty_unit": "quote_count", "volume_unit": None, "bar_available_at": None,
            "license_scope": "operator_account_only_pending_scope_verification"},
        "provenance": {"source_uri": SOURCE_URI, "catalog_source_uri": "https://teajoin.com/debug/api/fx_obasic",
            "payload_sha256": hashlib.sha256(payload).hexdigest() if isinstance(payload, bytes) else None,
            "catalog_sha256": hashlib.sha256(catalog_payload).hexdigest() if isinstance(catalog_payload, bytes) else None,
            "collected_at": collected_at.astimezone(timezone.utc).isoformat() if collected_at else None,
            "audited_at": as_of.astimezone(timezone.utc).isoformat(),
            "requested_start": start_date.isoformat(), "requested_end": end_date.isoformat(),
            "documentation": ["https://tushare.pro/document/2?doc_id=178", "https://tushare.pro/document/2?doc_id=179"]},
        "issues": list(issues.values())}
