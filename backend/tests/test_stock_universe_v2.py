from datetime import date

import pytest

from app.services.stock_research_v2.universe import HistoricalUniverse, universe_at


def universe(**changes):
    return HistoricalUniverse.model_validate({"pool_id": "qa-historical", "pool_version": "a" * 64, "start": "2026-01-01", "end": "2026-12-31", "members": [
        {"instrument_id": "000001.SZ", "known_on": "2025-01-01", "valid_from": "2025-01-01", "valid_to": "2026-06-01", "exit_known_on": "2026-05-20", "source_sha256": "a" * 64},
        {"instrument_id": "600000.SH", "known_on": "2026-03-01", "valid_from": "2026-03-01", "valid_to": None, "source_sha256": "b" * 64}], **changes})


def test_delisted_stock_remains_before_exit_and_new_listing_never_fills_older_dates():
    data = universe()
    assert universe_at(data, date(2026, 2, 1)) == ["000001.SZ"]
    assert universe_at(data, date(2026, 3, 1)) == ["000001.SZ", "600000.SH"]
    assert universe_at(data, date(2026, 6, 1)) == ["600000.SH"]


def test_explicit_coverage_overlap_identity_and_versions_fail_closed():
    with pytest.raises(ValueError, match="覆盖"):
        universe_at(universe(), date(2025, 12, 31))
    with pytest.raises(ValueError):
        universe_at(universe(), date(2026, 2, 1), expected_version="b" * 64)
    members = [universe().members[0].model_dump(mode="json")] * 2
    with pytest.raises(ValueError, match="重叠"):
        universe(members=members)
    with pytest.raises(ValueError):
        universe(members=[{"instrument_id": "000001.SH", "valid_from": "2026-01-01", "valid_to": None, "source_sha256": "a" * 64}])


def test_future_entry_does_not_change_past_signal_universe():
    data = universe()
    later = {"instrument_id": "000002.SZ", "known_on": "2026-09-28", "valid_from": "2026-10-01", "valid_to": None, "source_sha256": "c" * 64}
    extended = universe(members=[item.model_dump(mode="json") for item in data.members] + [later])
    assert universe_at(extended, date(2026, 3, 1)) == universe_at(data, date(2026, 3, 1))


def test_unknown_or_late_exit_evidence_cannot_retroactively_remove_a_member():
    member = universe().members[0].model_dump(mode="json")
    for published in (None, "2026-06-02"):
        with pytest.raises(ValueError, match="退出"):
            universe(members=[{**member, "exit_known_on": published}])
