import csv
import io
import json
import re
from uuid import UUID

from pydantic import ValidationError

from app.schemas.stock_report_artifacts import SavedResearchComparison, SavedResearchExport
from app.schemas.stock_research import _parse_date
from app.schemas.stock_research_jobs import MAX_RESULT_BYTES
from app.services.stock_research_jobs import JobError, ResearchJobService, job_view


MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_COMPARISON_BYTES = 2 * 1024 * 1024
BACKTEST_METRICS = (
    ("total_return_pct", "总收益", "percent"),
    ("annualized_return_pct", "年化收益", "percent"),
    ("annualized_vol_pct", "年化波动", "percent"),
    ("sharpe", "夏普比率", "ratio"),
    ("sortino", "索提诺比率", "ratio"),
    ("calmar", "卡玛比率", "ratio"),
    ("max_drawdown_pct", "最大回撤", "percent"),
    ("win_rate", "胜率", "percent"),
    ("turnover_rate", "平均换手比例", "ratio"),
    ("periods", "收益期数", "count"),
)
ANALYSIS_METRICS = (
    ("ic_mean", "IC均值", "ratio"), ("ic_std", "IC标准差", "ratio"),
    ("ic_ir", "IC IR", "ratio"), ("rank_ic_mean", "Rank IC均值", "ratio"),
    ("rank_ic_ir", "Rank IC IR", "ratio"),
    ("ic_positive_ratio", "IC正值比例", "ratio"), ("periods", "有效IC期数", "count"),
)


def bounded_json(value, limit: int) -> bytes:
    chunks, size = [], 0
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size > limit:
                raise JobError("report_too_large", "保存报告或导出文件超过安全大小上限；未截断或重新计算", 422)
            chunks.append(encoded)
    except JobError:
        raise
    except (TypeError, ValueError, RecursionError):
        raise JobError("invalid_saved_report", "保存报告包含非有限数值或无效结构，无法安全读取", 409) from None
    return b"".join(chunks)


async def saved_report(service: ResearchJobService, job_id: UUID) -> dict:
    row = await service.row(job_id, include_result=True)
    if row.status != "completed" or row.result_json is None:
        raise JobError("result_unavailable", "研究尚未成功完成，请查看任务状态或错误详情")
    if not isinstance(row.result_json, dict) or not row.result_json:
        raise JobError("invalid_saved_report", "已完成任务缺少有效保存结果", 409)
    if row.result_bytes > MAX_RESULT_BYTES:
        raise JobError("report_too_large", "保存报告超过8MiB安全上限", 422)
    bounded_json(row.result_json, MAX_RESULT_BYTES)
    bounded_json(row.params_json, 128 * 1024)
    bounded_json(row.data_manifest, 512 * 1024)
    result = {**row.result_json, "research_job_id": str(row.id), "data_version": row.data_version,
              "data_manifest": row.data_manifest, "workflow_version": row.workflow_version,
              "market": "stock", "research_only": True}
    try:
        return SavedResearchExport(job=job_view(row, details=True), result=result).model_dump(mode="json")
    except ValidationError:
        raise JobError("invalid_saved_report", "保存报告结构无效，未使用默认数据替代", 409) from None


def csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (float, int)):
        return str(value)
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    value = str(value)
    stripped = value.lstrip()
    if value.startswith(("\t", "\r", "\n")) or stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_csv(report: dict) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(["section", "key", "date", "code", "name", "value", "extra"])
    encoded_size = 3

    def write(section, key, value=None, *, date=None, code=None, name=None, extra=None):
        nonlocal encoded_size
        row_stream = io.StringIO(newline="")
        csv.writer(row_stream, lineterminator="\r\n").writerow([csv_cell(cell) for cell in (section, key, date, code, name, value, extra)])
        serialized = row_stream.getvalue()
        encoded_size += len(serialized.encode("utf-8"))
        if encoded_size > MAX_EXPORT_BYTES - 128:
            raise JobError("report_too_large", "CSV导出超过16MiB安全上限；未截断保存明细", 422)
        stream.write(serialized)

    write("metadata", "export_version", report["export_version"])
    write("metadata", "export_scope", "仅导出全部已保存明细；trades_truncated=true表示原计算已截断，未重建缺失历史")
    write("metadata", "provenance_limit", "数据指纹不证明不可变输入或PIT；无基准或实盘认证")
    for key, value in report["job"].items():
        if key == "params":
            for parameter, parameter_value in value.items():
                write("params", parameter, parameter_value)
        else:
            write("metadata", "job." + key, value)
    scalar_sections = {"metrics", "ic", "params", "method"}
    row_sections = {"nav", "long_short_nav", "trades", "ic_series", "quantile_returns", "decay", "skipped_rebalances", "factor_policies"}
    for key, value in report["result"].items():
        if key in scalar_sections and isinstance(value, dict):
            for metric, metric_value in value.items():
                write(key, metric, metric_value)
        elif key in row_sections and isinstance(value, list):
            for index, point in enumerate(value):
                if isinstance(point, dict):
                    value_key = next((field for field in ("nav", "factor_value", "avg_return", "ic", "rank_ic") if field in point), None)
                    write(key, str(index), point.get(value_key) if value_key else None,
                          date=point.get("date"), code=point.get("code"), name=point.get("name"),
                          extra={field: item for field, item in point.items() if field not in {"date", "code", "name", value_key}})
                else:
                    write(key, str(index), point)
        else:
            write("metadata", key, value)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


async def export_report(service: ResearchJobService, job_id: UUID, format: str) -> tuple[bytes, str]:
    report = await saved_report(service, job_id)
    body = bounded_json(report, MAX_EXPORT_BYTES) if format == "json" else export_csv(report)
    return body, report["job"]["method_version"] or "unrecorded"


def requested_period(job: dict) -> tuple[str, str] | None:
    try:
        parameters = job["params"]
        start, end = parameters.get("start_date"), parameters.get("end_date")
        if not isinstance(start, str) or not isinstance(end, str) or not start or not end:
            return None
        start_date, end_date = _parse_date(start), _parse_date(end)
        return (start_date.isoformat(), end_date.isoformat()) if start_date <= end_date else None
    except (ValueError, TypeError):
        return None


def summarize(report: dict) -> dict:
    result, job = report["result"], report["job"]
    metadata_keys = ("method_version", "workflow_version", "data_version", "validation_status", "validation_limits",
                     "analysis_limits", "method", "factor_id", "label", "group", "direction", "market", "research_only",
                     "trades_total", "trades_truncated", "rebalance_count", "entry_date", "entry_nav", "total_cost")
    summary = {"metadata": {key: result[key] for key in metadata_keys if key in result},
               "params": result.get("params", {}), "metrics": result.get("metrics") if job["kind"] == "backtest" else None,
               "ic": result.get("ic") if job["kind"] == "analysis" else None,
               "factor_policies": result.get("factor_policies", [])}
    return {"job": job, "result": summary}


async def compare_reports(service: ResearchJobService, left_id: UUID, right_id: UUID) -> dict:
    if left_id == right_id:
        raise JobError("same_report", "请选择两份不同的保存研究", 422)
    left, right = await saved_report(service, left_id), await saved_report(service, right_id)
    left_job, right_job = left["job"], right["job"]
    if left_job["kind"] != right_job["kind"]:
        raise JobError("different_kind", "仅能对照相同类型的保存研究", 422)
    flags = {}
    warnings = ["保存数据指纹不证明不可变输入或PIT；该对照仅为历史研究结果并列，不提供基准或实盘认证。",
                "配置、样本、费用及滑点可能不同；差值为右侧减左侧，百分比指标差值为百分点，不构成A/B因果结论。"]
    for flag, key, label in (("same_method", "method_version", "计算方法"), ("same_data_version", "data_version", "数据版本"), ("same_workflow", "workflow_version", "执行流程")):
        flags[flag] = bool(left_job.get(key) and right_job.get(key) and left_job[key] == right_job[key])
        if key == "method_version":
            flags[flag] = flags[flag] and all(report["result"].get(key) == report["job"].get(key) for report in (left, right))
        if key == "data_version":
            flags[flag] = flags[flag] and bool(re.fullmatch(r"[0-9a-f]{64}", left_job[key]))
        if not flags[flag]:
            warnings.append(label + "不同或未记录；仅并列展示，不计算差值。")
    left_period, right_period = requested_period(left_job), requested_period(right_job)
    flags["same_requested_period"] = bool(left_period and right_period and left_period == right_period)
    if not flags["same_requested_period"]:
        warnings.append("请求区间不同或未记录；仅并列展示，不计算差值。")
    comparable = all(flags.values())
    kind = left_job["kind"]
    summary_left, summary_right = summarize(left), summarize(right)
    metrics_key = "metrics" if kind == "backtest" else "ic"
    definitions = BACKTEST_METRICS if kind == "backtest" else ANALYSIS_METRICS
    metric_rows = []
    try:
        for key, label, unit in definitions:
            left_value = (summary_left["result"][metrics_key] or {}).get(key)
            right_value = (summary_right["result"][metrics_key] or {}).get(key)
            if any(value is not None and (isinstance(value, bool) or not isinstance(value, (float, int))) for value in (left_value, right_value)):
                raise ValueError("无效指标类型")
            metric_rows.append({"key": key, "label": label, "unit": unit, "left": left_value, "right": right_value,
                                "delta": right_value - left_value if comparable and left_value is not None and right_value is not None else None})
        curve_key = "nav" if kind == "backtest" else "long_short_nav"
        payload = SavedResearchComparison(kind=kind, left=summary_left, right=summary_right,
                                         compatibility={"comparable": comparable, **flags, "warnings": warnings},
                                         metrics=metric_rows,
                                         curves={"label": "组合净值" if kind == "backtest" else "多空净值",
                                                 "left": left["result"].get(curve_key, []), "right": right["result"].get(curve_key, [])}).model_dump(mode="json")
        for points in (payload["curves"]["left"], payload["curves"]["right"]):
            dates = [point["date"] for point in points]
            if dates != sorted(set(dates)):
                raise ValueError("净值日期重复或未排序")
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise JobError("invalid_saved_report", "保存报告指标或净值结构无效，未将未知数据当作零值", 409) from None
    bounded_json(payload, MAX_COMPARISON_BYTES)
    return payload
