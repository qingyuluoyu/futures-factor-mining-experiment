import csv
import hashlib
import importlib
import io
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.stock_research_jobs import SCHEMA_PATH, SCHEMA_VERSION
from app.core.deps import AuthContext, get_auth_context, get_session, get_settings
from app.models.stock_research_job import StockResearchJob, StockResearchSchemaVersion
from tests.test_stock_research_job_service import job_database


def sample_result(kind="backtest", **changes):
    result = {
        "method_version": "saved-method-v0", "validation_status": "research_only",
        "validation_limits": ["历史数据指纹不证明PIT"],
        "params": {"commission_rate": 0.0003}, "factor_policies": [],
    }
    if kind == "backtest":
        result.update(metrics={"total_return_pct": -2.0, "sharpe": 0.5, "calmar": None},
                      nav=[{"date": "2026-08-10", "nav": 0.98}],
                      trades=[{"date": "2026-08-10", "code": "000001", "name": "中文,名称\n二行", "factor_value": -1.25, "cost": 0.003} for _ in range(75)],
                      trades_total=99, trades_truncated=True)
    else:
        result.update(ic={"ic_mean": 0.1, "ic_positive_ratio": 0.25, "periods": 22},
                      long_short_nav=[{"date": "2026-08-10", "nav": 1.02}],
                      quantile_returns=[{"group": 0, "avg_return": -0.2}], ic_series=[{"date": "2026-08-10", "ic": -0.4}])
    return {**result, **changes}


async def save_report(factory, owner, *, kind="backtest", status="completed", result=None, method="saved-method-v0", workflow="saved-workflow-v0", version="a" * 64, params=None, stored_bytes=None, **changes):
    now = datetime.now(timezone.utc)
    payload = sample_result(kind) if result is None else result
    async with factory() as session:
        job = StockResearchJob(
            id=uuid4(), user_id=owner, trading_mode="virtual", kind=kind, title="保存研究",
            status=status, step=status, progress=100 if status != "pending" else 0,
            request_id="r" * 32, idempotency_key=uuid4(), request_hash="b" * 64,
            params_json=params or {"start_date": "20260713", "end_date": "2026-09-11", "factor_ids": ["ret_20d"]},
            method_version=method, workflow_version=workflow, data_version=version if status == "completed" else None,
            data_manifest={"data_version": version, "source": "隔离SQLite夹具"},
            result_json=payload if status == "completed" else None,
            result_bytes=(len(json.dumps(payload).encode()) if stored_bytes is None else stored_bytes) if status == "completed" else 0,
            created_at=now, updated_at=now, finished_at=now if status != "pending" else None,
            **changes,
        )
        session.add(job)
        await session.commit()
        return str(job.id)


@asynccontextmanager
async def report_http(*, authenticated=True, enabled=True):
    assert importlib.util.find_spec("app.api.stock_research_reports") is not None, "保存报告router尚未实现"
    module = importlib.import_module("app.api.stock_research_reports")
    async with job_database() as (factory, owner, other):
        async with factory() as session:
            session.add(StockResearchSchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
            await session.commit()
        app = FastAPI()
        app.include_router(module.router)
        current = {"user": owner, "mode": "virtual"}
        if authenticated:
            app.dependency_overrides[get_auth_context] = lambda: AuthContext(user=SimpleNamespace(id=current["user"], market_type="stock"), trading_mode=current["mode"])
        app.dependency_overrides[get_settings] = lambda: SimpleNamespace(stock_research_jobs_enabled=enabled)

        async def sessions():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = sessions
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, factory, owner, other, current


@pytest.mark.asyncio
async def test_export_preserves_saved_payload_versions_and_attachment_headers():
    async with report_http() as (client, factory, owner, _, _):
        report_id = await save_report(factory, owner)
        response = await client.get(f"/api/stock/research/jobs/{report_id}/export")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["export_version"] == "stock-research-export-v1"
        assert payload["job"]["method_version"] == "saved-method-v0"
        assert payload["result"]["method_version"] == "saved-method-v0"
        assert payload["result"]["trades"] == sample_result()["trades"]
        assert payload["result"]["trades_total"] == 99 and payload["result"]["trades_truncated"]
        assert payload["job"]["data_manifest"]["source"] == "隔离SQLite夹具"
        assert response.headers["X-Method-Version"] == "saved-method-v0"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Request-ID"]
        assert response.headers["Content-Disposition"] == f'attachment; filename="stock-research-{report_id}.json"'


@pytest.mark.asyncio
async def test_csv_exports_all_saved_rows_unicode_codes_and_safe_cells():
    async with report_http() as (client, factory, owner, _, _):
        result = sample_result()
        attacks = ["=1+1", "+SUM(A1)", "-CMD", "@formula", "\t=attack", "\rattack", "\nattack", "  =attack"]
        result["trades"].extend({"code": "000001", "name": value, "factor_value": -1.25} for value in attacks)
        report_id = await save_report(factory, owner, result=result)
        response = await client.get(f"/api/stock/research/jobs/{report_id}/export?format=csv")
        assert response.status_code == 200, response.text
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert list(rows[0]) == ["section", "key", "date", "code", "name", "value", "extra"]
        trades = [row for row in rows if row["section"] == "trades"]
        assert len(trades) == 83 and all(row["code"] == "000001" for row in trades)
        assert trades[0]["name"] == "中文,名称\n二行"
        assert all(row["name"].startswith("'") for row in trades[-len(attacks):])
        assert all(row["value"] == "-1.25" for row in trades)
        assert any(row["key"] == "trades_truncated" and row["value"] == "true" for row in rows)


@pytest.mark.asyncio
async def test_export_analysis_contains_groups_ic_and_saved_curve():
    async with report_http() as (client, factory, owner, _, _):
        report_id = await save_report(factory, owner, kind="analysis")
        response = await client.get(f"/api/stock/research/jobs/{report_id}/export?format=csv")
        assert response.status_code == 200, response.text
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        assert {"ic", "ic_series", "quantile_returns", "long_short_nav", "params", "metadata"} <= {row["section"] for row in rows}


@pytest.mark.asyncio
async def test_export_and_compare_enforce_auth_feature_and_owner_mode():
    for authenticated, enabled, expected in [(False, True, 401), (True, False, 503)]:
        async with report_http(authenticated=authenticated, enabled=enabled) as (client, _, _, _, _):
            for path in [f"/jobs/{uuid4()}/export", f"/compare?left_id={uuid4()}&right_id={uuid4()}"]:
                response = await client.get("/api/stock/research" + path)
                assert response.status_code == expected
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Request-ID"]
    async with report_http() as (client, factory, owner, other, current):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner)
        for user, mode in [(other, "virtual"), (owner, "live")]:
            current.update(user=user, mode=mode)
            for path in [f"/jobs/{left}/export", f"/compare?left_id={left}&right_id={right}"]:
                response = await client.get("/api/stock/research" + path)
                assert response.status_code == 404 and response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_report_input_and_terminal_validation():
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        for status in ["pending", "failed", "cancelled", "expired"]:
            other = await save_report(factory, owner, status=status)
            assert (await client.get(f"/api/stock/research/jobs/{other}/export")).status_code == 409
            assert (await client.get(f"/api/stock/research/compare?left_id={left}&right_id={other}")).status_code == 409
        assert (await client.get(f"/api/stock/research/jobs/{left}/export?format=xml")).status_code == 422
        assert (await client.get("/api/stock/research/jobs/bad/export")).status_code == 422
        assert (await client.get(f"/api/stock/research/compare?left_id={left}&right_id={left}")).status_code == 422
        mixed = await save_report(factory, owner, kind="analysis")
        assert (await client.get(f"/api/stock/research/compare?left_id={left}&right_id={mixed}")).status_code == 422


@pytest.mark.asyncio
async def test_comparison_uses_saved_versions_percent_point_deltas_and_bounded_summaries():
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner, params={"start_date": "2026-07-13", "end_date": "20260911"}, result=sample_result(metrics={"total_return_pct": 3.0, "sharpe": 1.5}))
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"comparison_version", "kind", "left", "right", "compatibility", "metrics", "curves"}
        assert payload["comparison_version"] == "stock-saved-comparison-v1"
        assert payload["compatibility"]["comparable"]
        metrics = {row["key"]: row for row in payload["metrics"]}
        assert metrics["total_return_pct"]["delta"] == 5.0
        assert metrics["total_return_pct"]["unit"] == "percent"
        assert metrics["sharpe"]["delta"] == 1.0
        assert metrics["calmar"]["left"] is None and metrics["calmar"]["delta"] is None
        assert "trades" not in payload["left"]["result"]
        assert "nav" not in payload["left"]["result"]
        assert payload["left"]["result"]["metadata"]["method_version"] == "saved-method-v0"
        assert payload["curves"]["left"] == sample_result()["nav"]
        assert any("PIT" in warning for warning in payload["compatibility"]["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,flag", [({"version": "b" * 64}, "same_data_version"), ({"method": "old-method"}, "same_method"), ({"workflow": "old-workflow"}, "same_workflow"), ({"params": {"start_date": "2026-07-14", "end_date": "2026-09-11"}}, "same_requested_period"), ({"params": {"factor_id": "ret_20d"}}, "same_requested_period")])
async def test_incompatible_reports_keep_values_but_never_calculate_delta(changes, flag):
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner, **changes)
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert not payload["compatibility"]["comparable"] and not payload["compatibility"][flag]
        assert payload["compatibility"]["warnings"]
        assert all(row["delta"] is None for row in payload["metrics"])


@pytest.mark.asyncio
async def test_analysis_ic_comparison_uses_ratio_units_and_unknowns_stay_null():
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner, kind="analysis")
        right = await save_report(factory, owner, kind="analysis", result=sample_result("analysis", ic={"ic_mean": 0.2, "ic_positive_ratio": 0.5}))
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 200, response.text
        metrics = {row["key"]: row for row in response.json()["metrics"]}
        assert metrics["ic_mean"]["delta"] == pytest.approx(0.1)
        assert metrics["ic_positive_ratio"]["unit"] == "ratio" and metrics["ic_positive_ratio"]["delta"] == 0.25
        assert metrics["periods"]["right"] is None and metrics["periods"]["delta"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [sample_result(metrics={"sharpe": float("inf")}), sample_result(metrics={"sharpe": "4.2"}), sample_result(nav=[{"date": "not-a-date", "nav": 1.0}])])
async def test_invalid_saved_numeric_values_and_curve_are_rejected_for_comparison(result):
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner, result=result)
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 409
        assert response.headers["X-Research-Error"] == "invalid_saved_report"


@pytest.mark.asyncio
async def test_export_and_comparison_reject_oversized_saved_result_without_requery():
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner, result=sample_result(notes="x" * (8 * 1024 * 1024)), stored_bytes=0)
        assert (await client.get(f"/api/stock/research/jobs/{right}/export")).status_code == 422
        assert (await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")).status_code == 422


@pytest.mark.asyncio
async def test_unknown_data_fingerprint_never_enables_deltas():
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner, version="legacy-unrecorded")
        right = await save_report(factory, owner, version="legacy-unrecorded")
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 200, response.text
        assert not response.json()["compatibility"]["same_data_version"]
        assert all(row["delta"] is None for row in response.json()["metrics"])


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{}, sample_result(metrics={"sharpe": True}), sample_result(nav=[{"date": "2026-08-10", "nav": 0}]), sample_result(nav=[{"date": "2026-08-10", "nav": 1.0}] * 2), sample_result(nav=[{"date": "2026-08-10", "nav": 1.0}] * 10001), sample_result(factor_policies=[{}] * 101), sample_result(metrics={str(index): index for index in range(33)})])
async def test_missing_result_and_summary_limits_do_not_silently_truncate(result):
    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner, result=result)
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 409, response.text
        assert response.headers["X-Research-Error"] == "invalid_saved_report"
        if not result:
            assert (await client.get(f"/api/stock/research/jobs/{right}/export")).status_code == 409


@pytest.mark.asyncio
async def test_comparison_payload_budget_and_export_budget_are_enforced(monkeypatch):
    import app.services.stock_report_artifacts as artifacts

    async with report_http() as (client, factory, owner, _, _):
        left = await save_report(factory, owner)
        right = await save_report(factory, owner)
        monkeypatch.setattr(artifacts, "MAX_COMPARISON_BYTES", 256)
        assert (await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")).status_code == 422
        monkeypatch.setattr(artifacts, "MAX_EXPORT_BYTES", 256)
        for format in ["json", "csv"]:
            response = await client.get(f"/api/stock/research/jobs/{left}/export?format={format}")
            assert response.status_code == 422
            assert response.headers["X-Research-Error"] == "report_too_large"


@pytest.mark.asyncio
async def test_metadata_and_output_limit_preserve_unknown_old_values_without_synthesis():
    async with report_http() as (client, factory, owner, _, _):
        result = sample_result()
        del result["method_version"]
        del result["params"]
        left = await save_report(factory, owner, result=result)
        right = await save_report(factory, owner, result=result)
        response = await client.get(f"/api/stock/research/compare?left_id={left}&right_id={right}")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert not payload["compatibility"]["same_method"]
        assert "method_version" not in payload["left"]["result"]["metadata"]
        assert payload["left"]["result"]["params"] == {}
        assert all(metric["delta"] is None for metric in payload["metrics"])


@pytest.mark.asyncio
async def test_validation_errors_also_have_download_security_headers():
    async with report_http() as (client, _, _, _, _):
        for path in ["/jobs/invalid/export?format=csv", f"/jobs/{uuid4()}/export?format=exe", "/compare?left_id=invalid&right_id=invalid"]:
            response = await client.get("/api/stock/research" + path)
            assert response.status_code == 422
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["Cache-Control"] == "no-store" and response.headers["X-Request-ID"]
