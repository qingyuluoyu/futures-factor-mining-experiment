import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def subject():
    path = ROOT / "scripts/audit-stock-eod-reconciliation.py"
    assert path.exists(), "Read-only fixed-version CLI is missing"
    specification = importlib.util.spec_from_file_location("reconciliation_cli_test", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("arguments", [[], ["--previous", "a" * 64], ["--previous", "latest", "--current", "b" * 64],
                                       ["--previous", "a" * 64, "--current", "a" * 64]])
def test_cli_requires_two_distinct_explicit_sha256_versions(arguments):
    with pytest.raises(SystemExit):
        subject().parse_arguments(arguments)


@pytest.mark.parametrize("host,port", [("example.com", 5433), ("127.0.0.1", 5432), ("::1", 5433)])
def test_cli_refuses_any_endpoint_other_than_dedicated_local_pg(host, port):
    with pytest.raises(ValueError):
        subject().validate_local_settings(SimpleNamespace(postgres_host=host, postgres_port=port))


def test_cli_persists_exclusive_json_without_exposing_report_to_stdout(tmp_path):
    (tmp_path / "logs").mkdir()
    report = {"status": "observed", "source_calls": 0, "model_calls": 0, "items": [{"code": "600101"}]}
    first = subject().write_report(report, tmp_path)
    second = subject().write_report(report, tmp_path)
    assert first != second and first.parent == tmp_path / "logs"
    assert json.loads(first.read_text(encoding="utf8")) == report


def test_cli_failure_contract_does_not_serialize_exception_or_traceback():
    result = subject().failure_report(ValueError("api_key=secret-fixture postgres://secret"))
    assert result["status"] == "failed" and result["error_type"] == "ValueError"
    assert "secret" not in json.dumps(result)
