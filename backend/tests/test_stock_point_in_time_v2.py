from datetime import datetime, timezone

import pytest

from app.services.stock_research_v2.point_in_time import Disclosure, TradingCalendar, select_asof


def record(**changes):
    return Disclosure.model_validate({"instrument_id": "000001.SZ", "report_period": "2025-12-31", "financial_basis": "consolidated", "announced_at": "2026-03-01T02:00:00Z", "available_at": "2026-03-01T02:00:00Z", "collected_at": "2026-10-23T04:00:00Z", "vintage": "original_filing", "vintage_reference": "qa-original-filing-v1", "source_sha256": "a" * 64, "values": {"roe": "12.1"}, **changes})


def test_later_revision_and_later_report_never_change_past_selection():
    original = record()
    decision = datetime(2026, 4, 1, tzinfo=timezone.utc)
    expected = select_asof([original], decision)
    revised = record(announced_at="2026-04-02T02:00:00Z", available_at="2026-04-02T02:00:00Z", vintage="revision_filing", source_sha256="b" * 64, values={"roe": "3.2"})
    newer = record(report_period="2026-03-31", announced_at="2026-04-10T02:00:00Z", available_at="2026-04-10T02:00:00Z", source_sha256="c" * 64)
    assert select_asof([revised, newer, original], decision) == expected
    assert select_asof([original, revised], datetime(2026, 4, 3, tzinfo=timezone.utc))[0].values["roe"] == "3.2"


def test_date_only_disclosure_requires_true_calendar_next_session_and_never_guesses_weekdays():
    original = record(announced_at=None, announced_on="2026-03-01", available_at=None)
    calendar = TradingCalendar.model_validate({"start": "2026-03-01", "end": "2026-03-05", "sessions": ["2026-03-03", "2026-03-05"], "version": "c" * 64})
    assert select_asof([original], datetime(2026, 3, 3, 1, 29, tzinfo=timezone.utc), calendar=calendar) == []
    assert select_asof([original], datetime(2026, 3, 3, 1, 30, tzinfo=timezone.utc), calendar=calendar)[0].values["roe"] == "12.1"
    with pytest.raises(ValueError, match="日历"):
        select_asof([original], datetime(2026, 3, 4, tzinfo=timezone.utc))


def test_revised_current_vendor_values_without_vintage_evidence_cannot_masquerade_as_historical():
    unverified = record(vintage="unverified")
    with pytest.raises(ValueError, match="原始披露"):
        select_asof([unverified], datetime(2026, 4, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="冲突"):
        select_asof([record(), record(source_sha256="b" * 64, values={"roe": "99"})], datetime(2026, 4, 1, tzinfo=timezone.utc))


def test_latest_report_missing_field_does_not_silently_fallback_to_older_report():
    later = record(report_period="2026-03-31", announced_at="2026-04-10T02:00:00Z", available_at="2026-04-10T02:00:00Z", values={"roe": None})
    result = select_asof([record(), later], datetime(2026, 4, 11, tzinfo=timezone.utc))
    assert result[0].values["roe"] is None
    with pytest.raises(ValueError):
        select_asof([record()], datetime(2026, 4, 1))


def test_conflict_detection_is_order_independent_at_the_selected_report_frontier():
    old = record()
    conflict = record(source_sha256="b" * 64, values={"roe": "99"})
    latest = record(report_period="2026-03-31", announced_at="2026-04-10T02:00:00Z", available_at="2026-04-10T02:00:00Z", source_sha256="c" * 64)
    decision = datetime(2026, 4, 11, tzinfo=timezone.utc)
    assert select_asof([old, conflict, latest], decision) == select_asof([latest, old, conflict], decision)


def test_unavailable_unverified_future_revision_does_not_change_the_current_signal():
    future = record(announced_at="2026-03-30T02:00:00Z", available_at="2026-04-02T02:00:00Z", vintage="unverified", source_sha256="b" * 64)
    decision = datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert select_asof([record(), future], decision) == select_asof([record()], decision)


def test_statement_basis_is_selected_explicitly_without_duplicate_instrument_rows():
    consolidated = record()
    parent = record(financial_basis="parent", source_sha256="b" * 64, values={"roe": "99"})
    decision = datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert select_asof([parent, consolidated], decision) == [consolidated]
    assert select_asof([consolidated, parent], decision, financial_basis="parent") == [parent]


def test_epoch_numbers_are_not_guessed_as_financial_timestamps():
    with pytest.raises(ValueError):
        record(announced_at=1772323200, available_at=1772323200)
    with pytest.raises(ValueError):
        record(report_period=1767139200)
    with pytest.raises(ValueError):
        select_asof([record()], 1775001600)
