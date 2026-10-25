from datetime import date, datetime, timezone
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.stock_eod import normalize_eod
from tests.test_stock_eod import catalog, row


def snapshot():
    return normalize_eod([row(), row(ts_code="000001.SZ"), row(ts_code="830001.BJ"), row(ts_code="600003.SH")],
        [*catalog(), {**catalog()[0], "code": "000001", "exchange": "SZSE"}, {**catalog()[0], "code": "830001", "exchange": "BSE"}],
        date(2026, 10, 16), acquired_at=datetime.now(timezone.utc), has_more=False)


def test_audit_selects_at_most_three_exchanges_and_excludes_quarantined_codes():
    from app.services.stock_eod_audit import select_sample

    stored = snapshot()
    assert select_sample(stored) == ["600001.SH", "000001.SZ", "830001.BJ"]
    stored["quotes"][0]["close"] = 1
    with pytest.raises(ValueError, match="校验"):
        select_sample(stored)


def test_audit_exact_match_checks_raw_price_units_not_adjusted_history_and_is_not_independent_certification():
    from app.services.stock_eod_audit import compare_sample

    stored = snapshot()
    before = deepcopy(stored)
    result = compare_sample(stored, [row()], ["600001.SH"])
    assert result["status"] == "matched" and result["matched_count"] == 1
    assert result["independent_verification"] is False
    assert result["price_adjustment"] == "none" and result["amount_unit"] == "CNY"
    assert result["stored_raw_response_evidence"] is False
    assert stored == before


def test_audit_reports_changed_prices_and_missing_quotes_without_silently_overwriting():
    from app.services.stock_eod_audit import compare_sample

    stored = snapshot()
    result = compare_sample(stored, [row(close=10, amount=1)], ["600001.SH", "000001.SZ"])
    assert result["status"] == "different"
    assert any(item["field"] == "close" and item["stored"] == 11 and item["observed"] == 10 for item in result["differences"])
    assert any(item["field"] == "amount" and item["stored"] == 456789 and item["observed"] == 1000 for item in result["differences"])
    assert any(item["field"] == "missing_from_source" for item in result["differences"])
    assert next(item for item in stored["quotes"] if item["code"] == "600001")["close"] == 11


@pytest.mark.parametrize("rows", [[row(ts_code="600002.SH")], [row(), row()], [row(trade_date="20261015")], [row(low=99)]])
def test_audit_rejects_unrequested_duplicate_wrong_date_or_invalid_prices(rows):
    from app.services.stock_eod_audit import compare_sample

    with pytest.raises(ValueError):
        compare_sample(snapshot(), rows, ["600001.SH"])


def test_audit_empty_source_is_explicit_no_data_not_zero_or_match():
    from app.services.stock_eod_audit import compare_sample

    result = compare_sample(snapshot(), [], ["600001.SH"])
    assert result["status"] == "no_data" and result["matched_count"] == 0


def test_cli_existing_output_fails_before_settings_or_paid_request(tmp_path, monkeypatch):
    import asyncio

    script = Path(__file__).resolve().parents[2] / "scripts/audit-stock-eod.py"
    assert script.is_file(), "Read-only stock audit entrypoint is missing"
    spec = importlib.util.spec_from_file_location("stock_eod_audit_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "existing.json"
    output.write_text("keep", encoding="utf-8")

    def unexpected():
        raise AssertionError("Existing output must stop before loading credentials")

    monkeypatch.setattr(module, "load_runtime_settings", unexpected)
    with pytest.raises(FileExistsError):
        asyncio.run(module.run(SimpleNamespace(output=str(output), date=None, verify_source=True)))
    assert output.read_text() == "keep"
