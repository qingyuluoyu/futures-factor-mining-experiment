from datetime import date, datetime, timezone

import pytest


def eod_module():
    import app.services.stock_eod as module

    return module


def row(**changes):
    return {"ts_code": "600001.SH", "trade_date": "20261016", "open": 10, "high": 12, "low": 9, "close": 11, "pre_close": 10, "pct_chg": 10, "vol": 123.45, "amount": 456.789, **changes}


def catalog():
    return [{"code": "600001", "name": "测试股", "exchange": "SSE", "industry": "金融", "list_date": date(2000, 1, 1)}, {"code": "600002", "name": "缺行情", "exchange": "SSE", "industry": "金融", "list_date": date(2000, 1, 1)}]


def test_eod_snapshot_has_precise_units_reference_price_provenance_and_missing_catalog():
    snapshot = eod_module().normalize_eod([row()], catalog(), date(2026, 10, 16), acquired_at=datetime(2026, 10, 18, tzinfo=timezone.utc), has_more=False)
    assert snapshot["quotes"][0]["volume"] == 123.45
    assert snapshot["quotes"][0]["amount"] == 456789
    assert snapshot["quotes"][0]["change_pct"] == 10
    assert snapshot["missing_codes"] == ["600002"]
    assert snapshot["covered_count"] == 1 and snapshot["expected_count"] == 2
    assert snapshot["price_adjustment"] == "none" and snapshot["volume_unit"] == "lot_100_shares"
    assert len(snapshot["data_version"]) == 64 and snapshot["collected_at"].startswith("2026-10-18")


@pytest.mark.parametrize("changes", [{"close": float("nan")}, {"open": 0}, {"low": 12}, {"vol": -1}, {"amount": -1}, {"ts_code": "600001.SZ"}, {"trade_date": "20261017"}, {"pre_close": 0}])
def test_eod_rejects_bad_units_dates_prices_and_identity(changes):
    with pytest.raises(ValueError):
        eod_module().normalize_eod([row(**changes)], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)


def test_eod_refuses_incomplete_source_duplicate_rows_and_makes_content_version_stable():
    module = eod_module()
    for has_more in (None, True):
        with pytest.raises(ValueError):
            module.normalize_eod([row()], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=has_more)
    with pytest.raises(ValueError):
        module.normalize_eod([row(), row()], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    first = module.normalize_eod([row()], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    second = module.normalize_eod([row()], list(reversed(catalog())), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    changed = module.normalize_eod([row(close=10)], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    assert first["data_version"] == second["data_version"] != changed["data_version"]


def test_eod_board_change_uses_vendor_ex_rights_reference_not_raw_previous_close():
    module = eod_module()
    snapshot = module.normalize_eod([row(pre_close=5)], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    result = module.market_from_eod(snapshot, catalog())
    assert result["items"][0]["change_pct"] == 120
    assert result["items"][1]["close"] is None
    assert result["coverage"] == {"instruments": 2, "quoted": 1, "comparable": 1}
    assert result["price_adjustment"] == "none" and result["source"] == "teajoin daily 盘后快照"


def test_eod_rejects_pre_listing_quotes_and_quarantines_unmatched_identifiers():
    module = eod_module()
    future_catalog = [{**catalog()[0], "list_date": date(2026, 10, 17)}]
    with pytest.raises(ValueError):
        module.normalize_eod([row()], future_catalog, date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    snapshot = module.normalize_eod([row(), row(ts_code="600003.SH")], catalog(), date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)
    changed_catalog = [*catalog(), {**catalog()[0], "code": "600003", "name": "后来新增目录"}]
    result = module.market_from_eod(snapshot, changed_catalog)
    assert result["coverage"]["quoted"] == 1
    assert next(item for item in result["items"] if item["code"] == "600003")["close"] is None
