from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import importlib
import importlib.util
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.stock_eod import METHOD_VERSION, content_hash, normalize_eod


def subject():
    name = "app.services.stock_eod_reconciliation"
    assert importlib.util.find_spec(name) is not None, "Fixed-version gap reconciliation is missing"
    return importlib.import_module(name)


def catalog(codes):
    return [{"code": code, "name": code, "exchange": "SSE", "industry": "fixture", "list_date": date(2000, 1, 1)} for code in codes]


def batch(stamp, codes, quoted):
    acquired = datetime.combine(stamp, datetime.min.time(), timezone.utc) + timedelta(hours=9)
    rows = [{"ts_code": code + ".SH", "trade_date": stamp.strftime("%Y%m%d"), "open": 10, "high": 10, "low": 10,
             "close": 10, "pre_close": 10, "vol": 123.45, "amount": 100} for code in quoted]
    snapshot = normalize_eod(rows, catalog(codes), stamp, acquired_at=acquired, has_more=False,
                             source_evidence={"private_token": "never-echo-fixture"})
    return SimpleNamespace(id=uuid4(), trade_date=stamp, method_version=METHOD_VERSION,
                           data_version=snapshot["data_version"], snapshot_json=snapshot,
                           finished_at=acquired + timedelta(seconds=1),
                           status="partially_completed" if snapshot["missing_codes"] or snapshot["unknown_codes"] else "completed")


@pytest.fixture
def pair():
    previous = batch(date(2024, 1, 3), ["600101", "600102", "600103", "600104"], ["600101", "600190"])
    current = batch(date(2024, 1, 4), ["600101", "600102", "600104", "600105"], ["600101", "600102", "600103", "600191"])
    return previous, current, catalog(["600101", "600102", "600104", "600105"]), datetime(2024, 1, 5, tzinfo=timezone.utc)


def test_gap_disappearance_is_not_automatically_repaired_or_reclassified(pair):
    previous, current, instruments, now = pair
    originals = deepcopy((previous.snapshot_json, current.snapshot_json, instruments))
    result = subject().build_reconciliation(previous, current, instruments, now=now)
    items = {row["code"]: row for row in result["items"]}
    assert items["600102"]["before"]["state"] == "missing"
    assert items["600102"]["after"]["state"] == "matched_quote"
    assert items["600103"]["after"]["state"] == "unknown"
    assert items["600190"]["after"]["state"] == "outside_snapshot"
    assert result["gap_sets"]["missing"] == {"persistent": ["600104"], "appeared": ["600105"], "no_longer_flagged": ["600102", "600103"]}
    assert result["gap_sets"]["unknown"] == {"persistent": [], "appeared": ["600103", "600191"], "no_longer_flagged": ["600190"]}
    assert result["batches"]["current"]["current_catalog_hash_matches"] is True
    assert result["batches"]["previous"]["current_catalog_hash_matches"] is False
    assert result["point_in_time_verified"] is False and result["eligible_for_backtest"] is False
    assert result["calendar_continuity_verified"] is False and result["independent_verification"] is False
    assert result["source_calls"] == result["model_calls"] == 0
    assert "private_token" not in str(result) and "never-echo-fixture" not in str(result)
    assert (previous.snapshot_json, current.snapshot_json, instruments) == originals


def test_new_current_catalog_annotation_never_promotes_old_unknown(pair):
    previous, current, instruments, now = pair
    instruments.extend(catalog(["600191"]))
    result = subject().build_reconciliation(previous, current, instruments, now=now)
    row = next(row for row in result["items"] if row["code"] == "600191")
    assert row["after"]["state"] == "unknown"
    assert row["current_catalog"]["status"] == "eligible_now"
    assert result["batches"]["current"]["current_catalog_hash_matches"] is False


def test_output_hash_is_stable_but_generation_time_is_explicit(pair):
    previous, current, instruments, now = pair
    first = subject().build_reconciliation(previous, current, instruments, now=now)
    second = subject().build_reconciliation(previous, current, list(reversed(instruments)), now=now + timedelta(hours=1))
    assert first["content_sha256"] == second["content_sha256"]
    assert first["generated_at"] != second["generated_at"]
    assert first["content_sha256"] == content_hash({key: value for key, value in first.items() if key not in ("content_sha256", "generated_at")})


@pytest.mark.parametrize("change", ["same_version", "reversed", "future", "naive_now", "bad_amount_unit", "bad_state",
                                   "bad_hash", "future_collected", "early_collected", "duplicate_catalog", "bad_catalog_exchange", "bad_catalog_date"])
def test_invalid_inputs_fail_closed_not_empty_or_latest_fallback(pair, change):
    previous, current, instruments, now = pair
    if change == "same_version":
        previous = current
    elif change == "reversed":
        previous, current = current, previous
    elif change == "future":
        now = datetime(2024, 1, 3, tzinfo=timezone.utc)
    elif change == "naive_now":
        now = now.replace(tzinfo=None)
    elif change == "bad_amount_unit":
        current.snapshot_json["amount_unit"] = "thousand_CNY"
    elif change == "bad_state":
        current.status = "completed"
    elif change == "bad_hash":
        current.snapshot_json["quotes"][0]["close"] = 9
    elif change == "future_collected":
        current.snapshot_json["collected_at"] = (now + timedelta(days=1)).isoformat()
    elif change == "early_collected":
        current.snapshot_json["collected_at"] = "2024-01-04T06:00:00+00:00"
    elif change == "duplicate_catalog":
        instruments.append(dict(instruments[0]))
    elif change == "bad_catalog_exchange":
        instruments[0]["exchange"] = "SZSE"
    else:
        instruments[0]["list_date"] = "20000101"
    if change == "bad_amount_unit":
        snapshot = current.snapshot_json
        current.data_version = snapshot["data_version"] = content_hash({key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")})
    with pytest.raises(ValueError):
        subject().build_reconciliation(previous, current, instruments, now=now)


def test_current_catalog_missing_date_or_post_listing_is_annotation_not_cause(pair):
    previous, current, instruments, now = pair
    instruments[0]["list_date"] = None
    instruments[-1]["list_date"] = date(2024, 1, 5)
    result = subject().build_reconciliation(previous, current, instruments, now=now)
    row = next(row for row in result["items"] if row["code"] == "600105")
    assert row["after"]["state"] == "missing"
    assert row["current_catalog"]["status"] == "listed_after_snapshot_now"
    assert row["cause"] == "unverified_requires_authorized_source_evidence"


@pytest.mark.parametrize("change", ["missing_collected", "null_collected", "unhashable_exchange"])
def test_malformed_stored_metadata_uses_validation_failure_contract(pair, change):
    previous, current, instruments, now = pair
    if change == "missing_collected":
        del current.snapshot_json["collected_at"]
    elif change == "null_collected":
        current.snapshot_json["collected_at"] = None
    else:
        instruments[0]["exchange"] = ["SSE"]
    with pytest.raises(ValueError):
        subject().build_reconciliation(previous, current, instruments, now=now)


def test_stored_timestamp_validation_never_echoes_raw_value(pair):
    previous, current, instruments, now = pair
    current.snapshot_json["collected_at"] = "fixture-secret-invalid-collected-at"
    with pytest.raises(ValueError) as caught:
        subject().build_reconciliation(previous, current, instruments, now=now)
    assert "fixture-secret" not in str(caught.value)
