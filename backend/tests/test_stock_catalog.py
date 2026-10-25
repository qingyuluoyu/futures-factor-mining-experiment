from datetime import date

import pytest

from app.services.stock_catalog import normalize_stock_catalog


def source_row(**changes):
    return {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "exchange": "SZSE", "industry": "银行", "list_date": "19910403", "list_status": "L", **changes}


def test_catalog_validates_exchange_identifier_and_optional_industry():
    rows = normalize_stock_catalog([source_row(), source_row(ts_code="600036.SH", symbol="600036", name="招商银行", exchange="SSE", industry=None)], today=date(2026, 10, 18))
    assert rows[0]["code"] == "000001" and rows[0]["list_date"] == date(1991, 4, 3)
    assert rows[1]["industry"] == ""


@pytest.mark.parametrize("changes", [{"ts_code": "000001.SH"}, {"exchange": "SSE"}, {"name": ""}, {"list_date": "20270101"}, {"list_date": "garbage"}, {"list_status": "D"}])
def test_catalog_rejects_conflicting_or_missing_metadata(changes):
    with pytest.raises(ValueError):
        normalize_stock_catalog([source_row(**changes)], today=date(2026, 10, 18))


def test_catalog_excludes_b_shares_and_rejects_duplicate_codes():
    rows = normalize_stock_catalog([source_row(), source_row(ts_code="900901.SH", symbol="900901", exchange="SSE")], today=date(2026, 10, 18))
    assert len(rows) == 1
    with pytest.raises(ValueError, match="重复"):
        normalize_stock_catalog([source_row(), source_row()], today=date(2026, 10, 18))
