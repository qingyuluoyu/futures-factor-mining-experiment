import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


def metals_module():
    import app.services.international_metals as module

    return module


def quote(**changes):
    return {"ts_code": "XAUUSD.FXCM", "trade_date": "20261015", "bid_open": 2000,
        "bid_high": 2010, "bid_low": 1990, "bid_close": 2005, "ask_open": 2001,
        "ask_high": 2011, "ask_low": 1991, "ask_close": 2006, "tick_qty": 123, "exchange": "FXCM", **changes}


def payload(rows=None, **changes):
    rows = [quote()] if rows is None else rows
    fields = list(quote())
    return json.dumps({"fields": fields, "items": [[row[field] for field in fields] for row in rows],
        "has_more": False, **changes}).encode()


def catalog():
    row = {"ts_code": "XAUUSD.FXCM", "classify": "METAL", "exchange": "FXCM",
        "min_unit": 1, "max_unit": 100, "pip": 0.01, "pip_cost": 1, "trading_hours": "fixture-session"}
    return json.dumps({"fields": list(row), "items": [list(row.values())], "has_more": False}).encode()


def audit(raw=None, **changes):
    return metals_module().audit_international_metals(payload() if raw is None else raw,
        **{"symbol": "XAU/USD", "start_date": date(2026, 7, 1), "end_date": date(2026, 10, 18),
            "as_of": datetime(2026, 10, 18, tzinfo=timezone.utc), **changes})


def codes(report):
    return {issue["code"] for issue in report["issues"]}


@pytest.mark.parametrize("symbol,expected", [("xauusd", "XAUUSD.FXCM"), ("XAU/USD", "XAUUSD.FXCM"),
    (" XAGUSD.FXCM ", "XAGUSD.FXCM"), ("xag/usd", "XAGUSD.FXCM")])
def test_whitelist_normalizes_international_metals(symbol, expected):
    assert metals_module().normalize_international_metal(symbol) == expected
    assert metals_module().is_international_metal(symbol)


@pytest.mark.parametrize("symbol", ["AU", "AG", "AU2612", "XAUUSD.OTHER", "EURUSD", "XAU USD", None, 1])
def test_whitelist_does_not_capture_domestic_contracts_or_unverified_sources(symbol):
    assert not metals_module().is_international_metal(symbol)
    with pytest.raises(ValueError):
        metals_module().normalize_international_metal(symbol)


def test_well_formed_quotes_and_catalog_pass_data_checks_without_enabling_financial_use():
    raw = payload()
    report = audit(raw, catalog_payload=catalog(), collected_at=datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert report["validation_status"] == "pending_certification"
    assert report["numerical_validation"] == "passed"
    assert report["validated_row_count"] == 1
    assert report["provenance"]["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["provenance"]["catalog_sha256"] == hashlib.sha256(catalog()).hexdigest()
    assert report["capabilities"] == {"market_reference": False, "simulation": False, "backtest": False}
    assert report["metadata"]["tick_qty_unit"] == "quote_count"
    assert report["metadata"]["currency"] == "USD"
    assert report["metadata"]["price_unit"] is None
    assert {"session_semantics_unverified", "contract_specification_unverified", "license_scope_unverified"} <= codes(report)
    assert "catalog_missing" not in codes(report)
    assert "volume" not in report and "quotes" not in report


@pytest.mark.parametrize("changes,issue", [({"bid_close": 0}, "invalid_price"), ({"ask_high": float("inf")}, "invalid_json"),
    ({"bid_open": True}, "invalid_price"), ({"bid_low": 2006}, "invalid_ohlc"),
    ({"ask_close": 2004}, "crossed_quote"), ({"ts_code": "XAGUSD.FXCM"}, "security_mismatch"),
    ({"trade_date": "20261019"}, "date_out_of_range"), ({"trade_date": "20260630"}, "date_out_of_range"),
    ({"trade_date": "20261301"}, "invalid_trade_date"), ({"trade_date": 20261015}, "invalid_trade_date"),
    ({"tick_qty": -1}, "invalid_tick_qty"), ({"tick_qty": 1.2}, "invalid_tick_qty"),
    ({"tick_qty": True}, "invalid_tick_qty"), ({"exchange": "OTHER"}, "dealer_mismatch")])
def test_rejects_invalid_quotes(changes, issue):
    report = audit(payload([quote(**changes)]))
    assert report["validation_status"] == "rejected"
    assert issue in codes(report)
    assert not any(report["capabilities"].values())


@pytest.mark.parametrize("raw,issue", [(b"not JSON", "invalid_json"), (b'{"fields":[],"fields":[]}', "invalid_json"),
    (b"[]", "invalid_envelope"), (payload(has_more=True), "incomplete_pagination"),
    (payload(fields=["ts_code", "ts_code"]), "invalid_fields"), (payload(items=[[1]]), "invalid_row_shape"),
    (payload([]), "no_data"), (payload([quote(), quote()]), "duplicate_session"),
    (payload([quote()] * 1001), "row_limit_exceeded"), (b" " * (2 * 1024 * 1024 + 1), "payload_limit_exceeded")],
    ids=["json", "duplicate_json_keys", "envelope", "pagination", "fields", "row_shape", "empty", "duplicate_session", "row_limit", "byte_limit"])
def test_rejects_invalid_bounded_envelopes(raw, issue):
    report = audit(raw)
    assert report["validation_status"] == "rejected"
    assert issue in codes(report)


def test_preserves_weekend_anomalies_and_missing_dealer_without_filling_defaults():
    report = audit(payload([quote(trade_date="20261010", exchange=None), quote(trade_date="20261011", exchange=None)]),
        catalog_payload=payload([]))
    assert report["validation_status"] == "pending_certification"
    assert report["observed"]["weekend_dates"] == ["2026-10-10", "2026-10-11"]
    assert report["validated_row_count"] == report["source_row_count"] == 2
    assert {"weekend_sessions_unverified", "dealer_missing", "catalog_empty"} <= codes(report)
    assert report["metadata"]["contract_multiplier"] is None
    assert report["metadata"]["session_calendar"] is None


def test_dates_are_checked_against_collection_time_and_future_requests():
    assert "date_after_collection" in codes(audit(collected_at=datetime(2026, 10, 14, tzinfo=timezone.utc)))
    assert "future_request_range" in codes(audit(end_date=date(2026, 10, 19)))
    with pytest.raises(ValueError):
        audit(as_of=datetime(2026, 10, 18))
    with pytest.raises(ValueError):
        audit(start_date=date(2026, 10, 19))


def test_unknown_collection_time_stays_null_and_cannot_be_replaced_by_audit_time():
    report = audit()
    assert report["provenance"]["collected_at"] is None
    assert report["provenance"]["audited_at"] == "2026-10-18T00:00:00+00:00"
    assert "collection_time_unverified" in codes(report)
    assert report["metadata"]["bar_available_at"] is None
    assert report["validation_status"] == "pending_certification"
    assert not any(report["capabilities"].values())


def test_raw_fingerprint_is_stable_and_changes_with_content():
    assert audit()["provenance"]["payload_sha256"] == audit()["provenance"]["payload_sha256"]
    assert audit()["provenance"]["payload_sha256"] != audit(payload([quote(tick_qty=124)]))["provenance"]["payload_sha256"]


def test_unfiltered_catalog_classification_conflict_is_preserved_for_certification():
    directory = json.loads(catalog())
    directory["items"][0][directory["fields"].index("classify")] = "COMMODITY"
    directory["items"][0][directory["fields"].index("pip")] = None
    report = audit(catalog_payload=json.dumps(directory).encode())
    assert report["validation_status"] == "pending_certification"
    assert {"catalog_classification_conflict", "catalog_specification_missing"} <= codes(report)
    assert "catalog_security_missing" not in codes(report)
    assert report["observed"]["catalog"]["classify"] == "COMMODITY"
    assert report["observed"]["catalog"]["pip"] is None
    assert not any(report["capabilities"].values())


@pytest.mark.parametrize("raw,issue", [(b"invalid", "catalog_invalid_json"),
    (catalog().replace(b"FXCM\"", b"OTHER\""), "catalog_security_missing"),
    (catalog().replace(b"METAL", b"FX"), "catalog_identity_mismatch")])
def test_catalog_anomalies_remain_explicit(raw, issue):
    assert issue in codes(audit(catalog_payload=raw))


def test_invalid_catalog_values_cannot_poison_serialized_audit_report():
    directory = json.loads(catalog())
    directory["items"][0][directory["fields"].index("pip")] = 1e308
    raw = json.dumps(directory).replace("1e+308", "1e999").encode()
    report = audit(catalog_payload=raw)
    assert "catalog_field_type_invalid" in codes(report)
    assert report["validation_status"] == "rejected"
    assert report["observed"]["catalog"]["pip"] is None
    json.dumps(report, allow_nan=False)


def test_cli_audits_local_files_emits_only_safe_summary_and_writes_report(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source.json"
    source.write_bytes(payload())
    report = tmp_path / "report.json"
    command = [sys.executable, str(root / "scripts" / "audit-international-metals.py"), "--symbol", "XAU/USD",
        "--payload", str(source), "--start", "2026-07-01", "--end", "2026-10-18", "--output", str(report)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert process.returncode == 2, process.stderr
    summary = json.loads(process.stdout)
    assert summary["validation_status"] == "pending_certification"
    assert summary["report_path"] == str(report.resolve())
    assert "bid_close" not in process.stdout and "2005" not in process.stdout
    assert json.loads(report.read_text())["source_row_count"] == 1
    repeated = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert repeated.returncode == 1
    assert "FileExistsError" in repeated.stderr
