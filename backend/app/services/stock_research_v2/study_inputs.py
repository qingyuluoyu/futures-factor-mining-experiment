"""Authorized immutable Parquet -> bounded calculation inputs, never data certification.

Call from a bounded research job. No public route or automatic qualification upgrade.
"""
from dataclasses import dataclass
from datetime import datetime, time
import logging
import re
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.schemas.stock_dataset_inspection import PanelQuery
from app.schemas.stock_research_v2 import FIELD_UNITS, KIND_FIELDS
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import descriptor
from app.services.stock_research_v2.columnar import ColumnarService
from app.services.stock_research_v2.point_in_time import Disclosure, TradingCalendar, select_asof, METHOD_VERSION as PIT_METHOD
from app.services.stock_research_v2.universe import HistoricalUniverse, universe_at, METHOD_VERSION as UNIVERSE_METHOD

METHOD_VERSION = "stock-columnar-study-inputs-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("qihuo.stock_study_inputs")


@dataclass(frozen=True)
class StudyInputs:
    fields: dict
    eligible: pd.DataFrame
    metadata: dict


async def load_study_inputs(registry, receipt, *, calendar, universe, fields, request_id, max_cells=2_000_000):
    if not isinstance(registry, ColumnarService) or not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise JobError("invalid_study_context", "研究需要当前用户/盘的列式服务和请求标识", 422)
    # Read through the actual registry first: client-supplied refs cannot bypass scope/hash checks.
    view = await registry.read(receipt)
    selection = view.manifest.selection
    if not isinstance(calendar, TradingCalendar) or not isinstance(universe, HistoricalUniverse):
        raise JobError("study_time_evidence_required", "研究输入必须绑定明确日历和历史股票池，未推测工作日或当前名单", 422)
    calendar = TradingCalendar.model_validate(calendar.model_dump())
    universe = HistoricalUniverse.model_validate(universe.model_dump())
    if universe.pool_id != selection.pool_id or universe.pool_version != selection.pool_version or not calendar.start <= selection.warmup_start <= selection.end <= calendar.end or not universe.start <= selection.warmup_start <= selection.end <= universe.end:
        raise JobError("study_time_evidence_mismatch", "日历/股票池版本或覆盖与固定数据选择不一致", 409)
    query = PanelQuery(fields=fields)
    fields = query.fields
    if "close" not in fields or type(max_cells) is not int or not 0 < max_cells <= 2_000_000:
        raise JobError("invalid_study_budget", "研究输入须包含close并符合单批预算", 422)
    sessions = [day for day in calendar.sessions if selection.warmup_start <= day <= selection.end]
    if not sessions:
        raise JobError("study_sessions_unavailable", "固定选择内没有明确交易日，未返回空成功", 422)
    instruments = list(selection.instruments)
    count = len(sessions) * len(instruments)
    if count * (len(fields) + 1) > max_cells:
        raise JobError("study_panel_budget_exceeded", "研究输入超过单批格数，须分块任务；未预分配全量面板", 422)
    context = {"method_version": METHOD_VERSION, "columnar": receipt.model_dump(mode="json"),
        "calendar": calendar.model_dump(mode="json"), "universe": universe.model_dump(mode="json"), "fields": fields}
    context_sha, _ = descriptor(context, 4 * 1024 * 1024)
    dates = pd.DatetimeIndex(sessions, dtype="datetime64[us]")
    panels = {field: pd.DataFrame(np.nan, index=dates, columns=instruments) for field in fields}
    eligible = pd.DataFrame(False, index=dates, columns=instruments)
    for day in sessions:
        members = set(universe_at(universe, day, expected_version=selection.pool_version)) & set(instruments)
        if members:
            eligible.loc[pd.Timestamp(day), sorted(members)] = True
    financial, seen, bases, anchors = [], {}, set(), set()
    selected, session_set = set(instruments), set(sessions)
    source_refs = []
    excluded_late = 0
    for partition in view.manifest.partitions:
        projected = sorted(set(fields) & KIND_FIELDS[partition.kind])
        if not projected:
            continue
        if partition.kind == "financial":
            # The newest filing frontier must survive projection even if it lacks
            # the selected metric. Otherwise an older ROE could be forward-filled.
            projected = sorted(KIND_FIELDS["financial"])
        metadata = view.source_metadata[str(partition.source_id)]
        if partition.kind == "financial" and metadata["financial_basis"] not in {"consolidated", "parent"}:
            raise JobError("study_financial_basis_unverified", "供应商财报口径未核实，未当合并报表使用", 422)
        if any(metadata["units"].get(field) != FIELD_UNITS[field] for field in projected if field in metadata["units"]):
            raise JobError("study_unit_mismatch", "研究字段单位与规范不同", 409)
        source_refs.append({"source_id": str(partition.source_id), "sha256": partition.source_sha256, "kind": partition.kind,
            "authorization_reference": metadata["authorization_reference"], "calendar_reference": metadata["calendar_reference"]})
        if partition.kind == "daily":
            bases.add(metadata["price_adjustment"])
            anchors.add(metadata["adjustment_anchor"])
            if len(bases) > 1 or len(anchors) > 1:
                raise JobError("study_price_basis_conflict", "固定来源混合原价/复权或锚点，未静默拼接", 409)
        # Financial effective/report dates can precede warmup. Do not filter them by
        # the daily range: historical disclosures must reach select_asof unchanged.
        for batch in view.store.iter_batches(partition, fields=projected, batch_rows=512):
            for row in batch:
                code, day = row["instrument_id"], row["effective_date"]
                if code not in selected:
                    continue
                values = {field: row[field] for field in projected if row.get(field + "__present")}
                if not values:
                    continue
                if partition.kind == "financial":
                    try:
                        financial.append(Disclosure(instrument_id=code, report_period=row["report_period"], financial_basis=metadata["financial_basis"],
                            announced_at=row["announced_at"], announced_on=row["announced_on"], available_at=row["available_at"],
                            collected_at=metadata["collected_at"], vintage=row["vintage"], vintage_reference=row["vintage_reference"], source_sha256=partition.source_sha256, values=values))
                    except (ValueError, TypeError):
                        raise JobError("study_disclosure_unverified", "财报缺少可验证披露/可得时点，未补时间或回填指标", 422) from None
                    if len(financial) > 100000:
                        raise JobError("study_disclosure_budget_exceeded", "披露记录超过单批预算", 422)
                    continue
                if not selection.warmup_start <= day <= selection.end:
                    continue
                if day not in session_set:
                    raise JobError("study_calendar_conflict", "观察行在明确日历中不是交易日，未当休市或改日期", 409)
                if row["available_at"] is not None and row["available_at"] > datetime.combine(day, time(15), SHANGHAI):
                    excluded_late += len(values)
                    continue
                for field, value in values.items():
                    key = (day, code, field)
                    if key in seen and seen[key] != value:
                        raise JobError("study_observation_conflict", "同日期证券字段有冲突来源，未按采集时间覆盖", 409)
                    seen[key] = value
                    panels[field].loc[pd.Timestamp(day), code] = float(value) if value is not None else np.nan
    financial_fields = set(fields) & KIND_FIELDS["financial"]
    if financial_fields:
        try:
            for day in sessions:
                for disclosure in select_asof(financial, datetime.combine(day, time(15), SHANGHAI), calendar=calendar):
                    for field in financial_fields:
                        value = disclosure.values.get(field)
                        panels[field].loc[pd.Timestamp(day), disclosure.instrument_id] = float(value) if value is not None else np.nan
        except ValueError:
            raise JobError("study_disclosure_unverified", "披露证据/时点冲突，未用当前财报或旧报告填补", 422) from None
    missing = [field for field in fields if not np.isfinite(panels[field].to_numpy()).any()]
    if missing:
        raise JobError("study_fields_unavailable", "固定研究输入缺少所选字段的有效值：" + ",".join(missing), 422)
    coverage = {field: {"finite": int(np.isfinite(panels[field].to_numpy()).sum()), "missing": int(panels[field].isna().to_numpy().sum())} for field in fields}
    metadata = {"method_version": METHOD_VERSION, "qualification": "observation_only", "research_enabled": False,
        "dataset": receipt.dataset.model_dump(mode="json"), "columnar_id": str(receipt.columnar_id), "input_sha256": context_sha,
        "calendar_version": calendar.version, "pool_version": universe.pool_version, "pit_method": PIT_METHOD, "universe_method": UNIVERSE_METHOD,
        "price_adjustment": next(iter(bases), None), "adjustment_anchor": next(iter(anchors), None), "fields": coverage,
        "eligible_cells": int(eligible.to_numpy().sum()), "excluded_late_values": excluded_late, "sources": source_refs,
        "limitations": ["日历/历史池输入及来源授权尚未由正式资格登记认证，不自动升级observation_only。", "浮点面板用于确定性研究，原始精确字符串保留在不可变来源/Parquet中；不作为现金成交账本。", "缺状态/公司行动/退市/独立指数证据，不授予可成交回测资格。"]}
    logger.info("request_id=%s user_id=%s mode=%s operation=study_inputs_loaded data_version=%s fields=%s cells=%s qualified=false", request_id, registry.scope.owner_id, registry.scope.trading_mode, receipt.dataset.data_version, len(fields), count)
    return StudyInputs(panels, eligible, metadata)
