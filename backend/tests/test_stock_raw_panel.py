from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import pytest

from app.services.stock_daily_archive import prepare_archive
from app.services.stock_eod import content_hash
from tests.test_stock_daily_archive import NOW, source_batch
from tests.test_stock_eod import row


def record():
    source = source_batch(quotes=[row(vol=0.01), row(ts_code="920000.BJ")])
    prepared = prepare_archive(source, now=NOW)
    return source, SimpleNamespace(**prepared["batch"]), prepared["rows"]


def module():
    return import_module("app.services.stock_raw_panel")


def test_raw_panel_preserves_decimal_units_missing_and_quarantined_not_backtest_eligible():
    source, batch, rows = record()
    result = module().build_raw_panel([(source, batch, rows)], [batch.version], ["600001.SH", "600002.SH", "920000.BJ", "000001.SZ"], now=NOW)
    assert result["observations"][0]["volume"] == "0.01"
    assert result["observations"][0]["amount"] == "456789"
    assert result["financial_metadata"]["price_adjustment"] == "none"
    assert result["financial_metadata"]["volume_unit"] == "lot_100_shares"
    assert result["eligible_for_backtest"] is False and result["point_in_time_verified"] is False
    assert result["data_available_at"] is None
    assert {item["instrument_id"]: item["reason"] for item in result["missing"]} == {
        "600002.SH": "source_missing", "920000.BJ": "source_unknown_quarantined", "000001.SZ": "outside_snapshot"}
    assert result["batches"][0]["verified_row_count"] == 1
    assert result["batches"][0]["source_data_version"] == source.data_version
    assert result["source_calls"] == 0 and result["model_calls"] == 0
    canonical = {key: value for key, value in result.items() if key not in ("content_sha256", "generated_at")}
    assert result["content_sha256"] == content_hash(canonical)
    assert "source_evidence" not in str(result)


@pytest.mark.parametrize("versions,instruments", [([], ["600001.SH"]), (["x" * 64], ["600001.SH"]),
    (["a" * 64] * 2, ["600001.SH"]), (["a" * 64], []), (["a" * 64], ["600001"]),
    (["a" * 64], ["600001.SZ"]), (["a" * 64], ["600001.SH"] * 2),
    ([format(index, "064x") for index in range(11)], ["600001.SH"])])
def test_raw_panel_rejects_invalid_or_unbounded_selection(versions, instruments):
    with pytest.raises(ValueError):
        module().build_raw_panel([], versions, instruments, now=NOW)


@pytest.mark.parametrize("change", ["source", "row", "extra_row", "missing_row", "metadata", "future", "naive", "not_decimal", "batch_id"])
def test_raw_panel_rejects_tampering_including_unselected_rows(change):
    source, batch, rows = record()
    if change == "source":
        source.snapshot_json["quotes"][0]["close"] = 10
    elif change == "row":
        rows[0]["volume"] = Decimal("999")
    elif change == "extra_row":
        rows.append(deepcopy(rows[0]))
    elif change == "missing_row":
        rows.clear()
    elif change == "metadata":
        batch.source_metadata["data_available_at"] = NOW.isoformat()
    elif change == "future":
        batch.archived_at = NOW + timedelta(minutes=1)
    elif change == "naive":
        batch.archived_at = NOW.replace(tzinfo=None)
    elif change == "not_decimal":
        rows[0]["volume"] = 0.01
    else:
        from uuid import uuid4
        rows[0]["batch_id"] = uuid4()
    with pytest.raises(ValueError):
        module().build_raw_panel([(source, batch, rows)], [batch.version], ["000001.SZ"], now=NOW)


def test_raw_panel_requires_all_pinned_versions_and_rejects_duplicate_trading_dates():
    first, second = record(), record()
    with pytest.raises(ValueError):
        module().build_raw_panel([first], [first[1].version, "a" * 64], ["600001.SH"], now=NOW)
    second[0].snapshot_json["quotes"][0]["volume"] = 1
    from tests.test_stock_daily_archive import rehash
    rehash(second[0])
    prepared = prepare_archive(second[0], now=NOW)
    second = second[0], SimpleNamespace(**prepared["batch"]), prepared["rows"]
    with pytest.raises(ValueError, match="交易日"):
        module().build_raw_panel([first, second], [first[1].version, second[1].version], ["600001.SH"], now=NOW)


def test_raw_panel_hash_does_not_depend_on_request_order_or_export_clock():
    item = record()
    first = module().build_raw_panel([item], [item[1].version], ["600001.SH", "600002.SH"], now=NOW)
    second = module().build_raw_panel([item], [item[1].version], ["600002.SH", "600001.SH"], now=NOW + timedelta(minutes=1))
    assert first["content_sha256"] == second["content_sha256"]
    assert first["generated_at"] != second["generated_at"]


def test_raw_panel_decimal_text_does_not_round_via_default_decimal_context():
    assert module().decimal_text(Decimal("12345678901234567890.123456789012345678")) == "12345678901234567890.123456789012345678"
    assert module().decimal_text(Decimal("0E-18")) == "0"
    assert module().decimal_text(Decimal("123.450000000000000000")) == "123.45"
