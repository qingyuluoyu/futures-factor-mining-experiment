from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.stock_eod import METHOD_VERSION, content_hash, normalize_eod
from tests.test_stock_eod import catalog, row


NOW = datetime(2026, 10, 19, 2, 15, tzinfo=timezone(timedelta(hours=8)))


def archive_module():
    import app.services.stock_daily_archive as module
    return module


def source_batch(*, quotes=None, **changes):
    snapshot = normalize_eod(quotes or [row(vol=123.45)], catalog(), date(2026, 10, 16), acquired_at=NOW, has_more=False)
    return SimpleNamespace(id=uuid4(), trade_date=date(2026, 10, 16), status="partially_completed", method_version=METHOD_VERSION,
        data_version=snapshot["data_version"], snapshot_json=snapshot, finished_at=NOW, **changes)


def rehash(source):
    snapshot = source.snapshot_json
    snapshot["data_version"] = content_hash({key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")})
    source.data_version = snapshot["data_version"]
    return source


def test_archive_keeps_fractional_lots_units_versions_and_uncertified_provenance():
    source = source_batch()
    prepared = archive_module().prepare_archive(source, now=NOW)
    batch, bars = prepared["batch"], prepared["rows"]
    assert batch["source_batch_id"] == source.id and batch["source_data_version"] == source.data_version
    assert batch["row_count"] == 1 and batch["expected_count"] == 2 and batch["missing_count"] == 1
    assert batch["source_collected_at"] == NOW and batch["archived_at"] == NOW
    assert batch["source_metadata"]["verification"] == "snapshot_only_not_independently_certified"
    assert batch["source_metadata"]["data_available_at"] is None
    assert bars[0]["volume"] == Decimal("123.45") and isinstance(bars[0]["volume"], Decimal)
    assert bars[0]["volume_unit"] == "lot_100_shares" and bars[0]["price_adjustment"] == "none"
    assert bars[0]["currency"] == "CNY" and bars[0]["amount_unit"] == "CNY"
    assert archive_module().prepare_archive(source, now=NOW + timedelta(minutes=1))["batch"]["version"] == batch["version"]
    assert source.snapshot_json["quotes"][0]["volume"] == 123.45


def test_archive_quarantines_unknown_without_inventing_history_or_relabelling_coverage():
    source = source_batch(quotes=[row(vol=0.01), row(ts_code="920000.BJ", vol=456.78)])
    prepared = archive_module().prepare_archive(source, now=NOW)
    assert len(prepared["rows"]) == 1 and prepared["rows"][0]["volume"] == Decimal("0.01")
    assert prepared["batch"]["unknown_count"] == 1 and prepared["batch"]["missing_count"] == 1
    assert prepared["batch"]["source_metadata"]["source_row_count"] == 2
    assert prepared["rows"][0]["instrument_id"].endswith(".SH")


@pytest.mark.parametrize("field,value", [("volume", True), ("volume", float("nan")), ("volume", -1),
    ("close", 0), ("close", float("inf")), ("amount", -1), ("volume", 1e20), ("volume", 1e-19),
    ("high", 1), ("instrument_id", "600001.SZ"), ("trade_date", "2026-10-15"), ("code", "000001")])
def test_archive_revalidates_price_identity_and_precision_even_with_a_recomputed_snapshot_hash(field, value):
    source = source_batch()
    source.snapshot_json["quotes"][0][field] = value
    if isinstance(value, float) and not value == value or value == float("inf"):
        with pytest.raises((ValueError, TypeError)):
            archive_module().prepare_archive(source, now=NOW)
    else:
        rehash(source)
        with pytest.raises(ValueError):
            archive_module().prepare_archive(source, now=NOW)


@pytest.mark.parametrize("field,value", [("currency", "USD"), ("market", "FX"), ("frequency", "minute"),
    ("price_adjustment", "qfq"), ("volume_unit", "share"), ("amount_unit", "thousand_CNY"),
    ("timezone", "UTC"), ("provider", "unknown"), ("source_uri", "https://unknown.invalid"),
    ("expected_count", 3), ("covered_count", True), ("missing_codes", []), ("unknown_codes", ["920000", "920000"]),
    ("vendor_has_more", True)])
def test_archive_rejects_mixed_financial_metadata_or_invalid_coverage(field, value):
    source = source_batch()
    source.snapshot_json[field] = value
    rehash(source)
    with pytest.raises(ValueError):
        archive_module().prepare_archive(source, now=NOW)


def test_archive_rejects_unfinished_tampered_future_and_unclosed_data():
    module = archive_module()
    source = source_batch()
    source.status = "failed"
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)
    source.status = "partially_completed"
    source.snapshot_json["quotes"][0]["close"] = 999
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)
    source = source_batch()
    source.finished_at = None
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)
    source = source_batch()
    source.snapshot_json["collected_at"] = (NOW + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)
    source = source_batch()
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW - timedelta(days=4))


def test_archive_rejects_duplicates_overlaps_and_status_that_hides_gaps():
    module = archive_module()
    source = source_batch()
    source.snapshot_json["quotes"].append(dict(source.snapshot_json["quotes"][0]))
    rehash(source)
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)
    source = source_batch()
    source.snapshot_json["missing_codes"].append(source.snapshot_json["quotes"][0]["code"])
    rehash(source)
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)
    source = source_batch()
    source.status = "completed"
    with pytest.raises(ValueError):
        module.prepare_archive(source, now=NOW)


@pytest.mark.parametrize("value", [10 ** 1000, "123.45", None, {}, [], -float("inf")])
def test_archive_invalid_json_numbers_fail_with_a_business_error(value):
    source = source_batch()
    source.snapshot_json["quotes"][0]["volume"] = value
    if not isinstance(value, float):
        rehash(source)
    with pytest.raises(ValueError):
        archive_module().prepare_archive(source, now=NOW)


@pytest.mark.parametrize("evidence", [[], "unstructured", {"unexpected": "x" * 65536}])
def test_archive_evidence_metadata_is_bounded_and_structured(evidence):
    source = source_batch()
    source.snapshot_json["source_evidence"] = evidence
    rehash(source)
    with pytest.raises(ValueError):
        archive_module().prepare_archive(source, now=NOW)


@pytest.mark.parametrize("days_later", [0, 1])
def test_archive_never_promotes_a_same_day_midday_snapshot_to_daily_close(days_later):
    source = source_batch()
    source.trade_date = NOW.date()
    source.snapshot_json["trade_date"] = NOW.date().isoformat()
    source.snapshot_json["quotes"][0]["trade_date"] = NOW.date().isoformat()
    collected = NOW.replace(hour=12)
    source.snapshot_json["collected_at"] = collected.isoformat()
    source.finished_at = collected
    rehash(source)
    with pytest.raises(ValueError):
        archive_module().prepare_archive(source, now=NOW.replace(hour=17) + timedelta(days=days_later))
