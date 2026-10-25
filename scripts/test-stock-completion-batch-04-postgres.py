import asyncio
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, select, update


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("completion_pg_harness", ROOT / "scripts/test-stock-completion-batch-02-postgres.py")
harness = importlib.util.module_from_spec(specification)
specification.loader.exec_module(harness)

from app.api import stock_research_jobs as job_api
from app.api.stock_research_reports import router as report_router
from app.core.deps import get_settings
from app.models.stock_research_job import StockResearchJob, StockResearchSchemaVersion
from app.models.user import User
from app.schemas.stock_research_jobs import CreateResearchJob, WORKFLOW_VERSION
from app.services.stock_factors.engine import METHOD_VERSION
from app.services.stock_research_jobs import ResearchJobService, claim_next_job, finish_job, release_worker_slot


def saved_fixture(kind="backtest", total_return=-2.0, *, method=METHOD_VERSION):
    result = {"method_version": method, "validation_status": "research_only",
              "validation_limits": ["确定性私有schema保存报告夹具，不是行情或策略认证"],
              "factor_policies": [], "params": {"commission_rate": 0.0003, "slippage_rate": 0.001}}
    if kind == "backtest":
        result.update(metrics={"total_return_pct": total_return, "sharpe": 0.5, "calmar": None},
                      nav=[{"date": "2026-08-10", "nav": 1.0 + total_return / 100}],
                      trades=[{"date": "2026-08-10", "signal_date": "2026-08-07", "code": "000001",
                               "name": "中文,名称\n二行" if index < 74 else "=1+1", "factor_value": -1.25,
                               "cost": 0.0003, "action": "买入", "weight": 0.1} for index in range(75)],
                      trades_total=99, trades_truncated=True)
    else:
        result.update(ic={"ic_mean": 0.1, "ic_positive_ratio": 0.25, "periods": 22},
                      long_short_nav=[{"date": "2026-08-10", "nav": 1.02}],
                      quantile_returns=[{"group": 0, "avg_return": -0.2}])
    return result


def creation(kind="backtest"):
    parameters = {"start_date": "2026-07-13", "end_date": "2026-09-11",
                  "preprocess": {"winsorize": "3sigma", "standardize": False, "fillna": False,
                                 "industry_neutral": False, "mktcap_neutral": False}}
    if kind == "backtest":
        parameters.update(factor_ids=["ret_20d"], rebalance="week", top_n=3, weight="factor",
                          commission_rate=0.0003, slippage_rate=0.001)
    else:
        parameters.update(factor_id="ret_20d", horizon=5, groups=3)
    return TypeAdapter(CreateResearchJob).validate_python({"kind": kind, "idempotency_key": str(uuid4()), "parameters": parameters})


async def additional_checks(*, check, scoped, application, client, headers, owner_id, other_id, stock_id, settings):
    async with scoped() as session:
        await session.execute(update(User).where(User.id == other_id).values(market_type="stock"))
        await session.commit()
    application.include_router(job_api.router)
    application.include_router(report_router)
    enabled_settings = settings.model_copy(update={"stock_research_jobs_enabled": True})
    application.dependency_overrides[get_settings] = lambda: enabled_settings
    base = "/api/stock/research"

    for path in (f"/jobs/{uuid4()}/export", f"/compare?left_id={uuid4()}&right_id={uuid4()}"):
        response = await client.get(base + path)
        check("b04_g1_report_requires_real_jwt", response.status_code == 401 and response.headers.get("X-Content-Type-Options") == "nosniff")
        response = await client.get(base + path, headers=headers(stock_id))
        check("b04_g1_report_missing_checksum_is_503", response.status_code == 503 and response.headers.get("Cache-Control") == "no-store" and bool(response.headers.get("X-Request-ID")))
    async with scoped() as session:
        session.add(StockResearchSchemaVersion(version=job_api.SCHEMA_VERSION, checksum=hashlib.sha256(job_api.SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()
    available = await client.get(base + "/availability", headers=headers(stock_id))
    check("b04_g1_report_capability_advertised_only_when_ready_and_registered", available.status_code == 200 and available.json()["saved_report_artifacts"] is True)

    async def persist(kind="backtest", *, total_return=-2.0, data_version="a" * 64):
        body = creation(kind)
        async with scoped() as session:
            saved = await ResearchJobService(session, stock_id, "virtual").create(body, request_id=uuid4().hex)
            await session.commit()
        job_id = UUID(saved["id"])
        async with scoped() as session:
            claimed = await claim_next_job(session)
            check("b04_g2_report_claimed_through_real_state_machine", claimed is not None and claimed.id == job_id and claimed.status == "running")
            token = claimed.worker_token
            await session.commit()
        result = saved_fixture(kind, total_return)
        manifest = {"data_version": data_version, "source": "synthetic_private_schema_saved_report_fixture",
                    "immutable_archive": False, "point_in_time_certified": False}
        async with scoped() as session:
            check("b04_g2_report_completed_through_finish_job", await finish_job(session, job_id, token, result=result, manifest=manifest))
            check("b04_g2_worker_slot_released_after_completion", await release_worker_slot(session, job_id, token))
            await session.commit()
        return str(job_id), saved["params"], result

    left, saved_parameters, original_result = await persist()
    right, _, _ = await persist(total_return=3.0)
    different_data, _, _ = await persist(total_return=3.0, data_version="b" * 64)
    analysis, _, _ = await persist("analysis")
    now = datetime.now(timezone.utc)
    legacy_id = uuid4()
    legacy_method = "stock-report-explicit-legacy-fixture-v0"
    legacy_result = saved_fixture(method=legacy_method)
    async with scoped() as session:
        session.add(StockResearchJob(id=legacy_id, user_id=stock_id, trading_mode="virtual", kind="backtest",
                                    title="明确旧方法夹具", status="completed", step="completed", progress=100,
                                    request_id=uuid4().hex, idempotency_key=uuid4(), request_hash="c" * 64,
                                    params_json=saved_parameters, method_version=legacy_method, workflow_version=WORKFLOW_VERSION,
                                    data_version="a" * 64, data_manifest={"data_version": "a" * 64, "source": "explicit_legacy_insert_fixture"},
                                    result_json=legacy_result, result_bytes=len(json.dumps(legacy_result).encode()),
                                    attempts=1, started_at=now, created_at=now, updated_at=now, finished_at=now))
        await session.commit()

    async with scoped() as session:
        rows = (await session.execute(select(StockResearchJob).order_by(StockResearchJob.id))).scalars().all()
        before = {str(row.id): json.dumps({"params": row.params_json, "result": row.result_json,
                                          "manifest": row.data_manifest, "method": row.method_version}, ensure_ascii=False, sort_keys=True) for row in rows}

    response = await client.get(f"{base}/jobs/{left}/export?format=json", headers=headers(stock_id))
    payload = response.json()
    check("b04_g2_json_saved_values_and_manifest_preserved", response.status_code == 200 and payload["result"]["trades"] == original_result["trades"] and payload["job"]["params"] == saved_parameters and payload["result"]["data_manifest"]["source"] == "synthetic_private_schema_saved_report_fixture")
    check("b04_g2_json_attachment_security_trace", response.headers.get("Content-Disposition") == f'attachment; filename="stock-research-{left}.json"' and response.headers.get("Cache-Control") == "no-store" and response.headers.get("X-Content-Type-Options") == "nosniff" and bool(response.headers.get("X-Request-ID")))
    legacy = await client.get(f"{base}/jobs/{legacy_id}/export?format=json", headers=headers(stock_id))
    check("b04_g2_export_preserves_original_legacy_method", legacy.status_code == 200 and legacy.headers.get("X-Method-Version") == legacy_method and legacy.json()["result"]["method_version"] == legacy_method and legacy.json()["job"]["method_version"] == legacy_method)
    response = await client.get(f"{base}/jobs/{left}/export?format=csv", headers=headers(stock_id))
    csv_rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    trades = [row for row in csv_rows if row["section"] == "trades"]
    check("b04_g2_csv_all_75_saved_trades_not_screen_60", response.status_code == 200 and len(trades) == 75)
    check("b04_g2_csv_chinese_comma_newline_leading_zero", trades[0]["name"] == "中文,名称\n二行" and all(row["code"] == "000001" for row in trades))
    check("b04_g2_csv_formula_escaped_numeric_negative_retained", trades[-1]["name"] == "'=1+1" and all(row["value"] == "-1.25" for row in trades))
    check("b04_g2_csv_saved_truncation_remains_visible", any(row["key"] == "trades_truncated" and row["value"] == "true" for row in csv_rows) and any(row["key"] == "trades_total" and row["value"] == "99" for row in csv_rows))

    compared = await client.get(f"{base}/compare?left_id={left}&right_id={right}", headers=headers(stock_id))
    payload = compared.json()
    metric = next(row for row in payload["metrics"] if row["key"] == "total_return_pct")
    check("b04_g3_equal_saved_versions_allow_percentage_point_delta", compared.status_code == 200 and payload["compatibility"]["comparable"] and metric["unit"] == "percent" and metric["delta"] == 5.0)
    check("b04_g3_null_is_never_zero_and_trades_not_duplicated", next(row for row in payload["metrics"] if row["key"] == "calmar")["left"] is None and "trades" not in payload["left"]["result"] and payload["curves"]["left"] == original_result["nav"])
    check("b04_g3_pit_and_causality_limits_are_explicit", any("PIT" in value for value in payload["compatibility"]["warnings"]) and any("因果" in value for value in payload["compatibility"]["warnings"]))
    for candidate, flag in ((different_data, "same_data_version"), (str(legacy_id), "same_method")):
        response = await client.get(f"{base}/compare?left_id={left}&right_id={candidate}", headers=headers(stock_id))
        payload = response.json()
        check("b04_g3_different_" + flag + "_has_no_deltas", response.status_code == 200 and not payload["compatibility"]["comparable"] and not payload["compatibility"][flag] and all(row["delta"] is None for row in payload["metrics"]))
    check("b04_g3_same_job_rejected_422", (await client.get(f"{base}/compare?left_id={left}&right_id={left}", headers=headers(stock_id))).status_code == 422)
    check("b04_g3_mixed_kind_rejected_422", (await client.get(f"{base}/compare?left_id={left}&right_id={analysis}", headers=headers(stock_id))).status_code == 422)

    for identity, mode in ((other_id, "virtual"), (stock_id, "live")):
        for path in (f"/jobs/{left}/export?format=json", f"/jobs/{left}/export?format=csv", f"/compare?left_id={left}&right_id={right}", f"/jobs/{left}"):
            response = await client.get(base + path, headers=headers(identity, mode))
            check("b04_g4_report_and_restore_owner_jwt_mode_isolated", response.status_code == 404)
    restored = await client.get(f"{base}/jobs/{left}", headers=headers(stock_id))
    check("b04_g4_restore_get_keeps_complete_saved_config", restored.status_code == 200 and restored.json()["params"] == saved_parameters and restored.json()["method_version"] == METHOD_VERSION)
    async with scoped() as session:
        rows = (await session.execute(select(StockResearchJob).order_by(StockResearchJob.id))).scalars().all()
        after = {str(row.id): json.dumps({"params": row.params_json, "result": row.result_json,
                                         "manifest": row.data_manifest, "method": row.method_version}, ensure_ascii=False, sort_keys=True) for row in rows}
        check("b04_g4_all_five_saved_reports_immutable_after_reads", before == after and await session.scalar(select(func.count()).select_from(StockResearchJob)) == 5)
        pending = await ResearchJobService(session, stock_id, "virtual").create(creation(), request_id=uuid4().hex)
        await session.commit()
    pending_id = pending["id"]
    check("b04_g4_pending_report_not_exportable_409", (await client.get(f"{base}/jobs/{pending_id}/export", headers=headers(stock_id))).status_code == 409)
    check("b04_g4_pending_report_not_comparable_409", (await client.get(f"{base}/compare?left_id={left}&right_id={pending_id}", headers=headers(stock_id))).status_code == 409)
    async with scoped() as session:
        await ResearchJobService(session, stock_id, "virtual").cancel(UUID(pending_id))
        await session.commit()


if __name__ == "__main__":
    asyncio.run(harness.main(additional_checks=additional_checks, report_label="b04"))
