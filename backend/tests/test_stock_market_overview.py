from datetime import date

import pytest

from app.services.stock_market import build_market_snapshot, filter_members


def bar(code, day, close):
    return {"code": code, "trade_date": day, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


def test_boards_are_sample_weighted_changes_not_fake_industry_indices():
    previous, current = date(2026, 10, 15), date(2026, 10, 16)
    instruments = [{"code": code, "name": code, "exchange": "SSE", "industry": "银行"} for code in ("600001", "600002", "600003")]
    rows = [bar("600001", previous, 10), bar("600001", current, 11), bar("600002", previous, 20), bar("600002", current, 18)]
    snapshot = build_market_snapshot(instruments, rows, current, previous)
    board = snapshot["boards"][0]
    assert board["count"] == 3 and board["quoted_count"] == board["comparable_count"] == 2
    assert board["change_pct"] == pytest.approx(0)
    assert board["advancing"] == board["declining"] == 1 and board["flat"] == 0
    assert board["leader"]["code"] == "600001"
    assert snapshot["coverage"] == {"instruments": 3, "quoted": 2, "comparable": 2}


def test_missing_or_invalid_quotes_are_null_not_zero_or_futures_fallback():
    day = date(2026, 10, 16)
    instruments = [{"code": "600001", "name": "样本", "exchange": "SSE", "industry": ""}]
    for rows in ([], [bar("600001", day, float("nan"))], [bar("600001", day, 0)]):
        snapshot = build_market_snapshot(instruments, rows, day, date(2026, 10, 15))
        assert snapshot["items"][0]["close"] is None
        assert snapshot["boards"][0]["change_pct"] is None
        assert snapshot["boards"][0]["comparable_count"] == 0


def test_current_quote_without_previous_day_never_invents_daily_change():
    day = date(2026, 10, 16)
    snapshot = build_market_snapshot([{"code": "000001", "name": "平安", "exchange": "SZSE", "industry": "银行"}], [bar("000001", day, 10)], day, date(2026, 10, 15))
    assert snapshot["items"][0]["close"] == 10
    assert snapshot["items"][0]["change_pct"] is None
    assert snapshot["items"][0]["instrument_id"] == "000001.SZ"


def test_member_search_sort_and_coverage_filters_keep_nulls_last():
    items = [{"code": "600001", "name": "银行A", "industry": "银行", "data_available": True, "change_pct": 1}, {"code": "600002", "name": "银行B", "industry": "银行", "data_available": False, "change_pct": None}, {"code": "600003", "name": "科技", "industry": "软件", "data_available": True, "change_pct": 2}]
    assert [row["code"] for row in filter_members(items, industry="银行", sort="change_pct", direction="desc")] == ["600001", "600002"]
    assert [row["code"] for row in filter_members(items, keyword="a", data_only=True)] == ["600001"]


def test_missing_latest_quote_does_not_mislabel_existing_history_as_catalog_only():
    current, previous = date(2026, 10, 16), date(2026, 10, 15)
    snapshot = build_market_snapshot([{"code": "600001", "name": "样本", "exchange": "SSE", "industry": "银行", "last_trade_date": date(2026, 10, 12)}], [], current, previous)
    row = snapshot["items"][0]
    assert row["data_status"] == "stale_quote"
    assert row["last_trade_date"] == "2026-10-12"
    assert row["close"] is None and row["change_pct"] is None
