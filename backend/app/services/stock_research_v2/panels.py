from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product
from uuid import UUID

from app.schemas.stock_dataset_inspection import PanelQuery
from app.schemas.stock_research_v2 import DatasetRef, KIND_FIELDS
from app.services.stock_research_jobs import JobError

METHOD_VERSION = "stock-observation-panel-v1"
MAX_PANEL_CELLS = 200000


def eligible_value(field, value):
    if value is not None and field in {"pe_ttm", "pb", "marketcap"} and Decimal(value) <= 0:
        return None
    return value


@dataclass(frozen=True)
class ObservedPanel:
    dates: tuple[str, ...]
    instruments: tuple[str, ...]
    fields: tuple[str, ...]
    cells: dict
    batch_rows: int
    summary: dict

    def batches(self):
        batch = []
        for day, instrument in product(self.dates, self.instruments):
            values = self.cells.get((day, instrument), {})
            batch.append({"date": day, "instrument_id": instrument, "values": {field: eligible_value(field, values.get(field)) for field in self.fields}})
            if len(batch) == self.batch_rows:
                yield batch
                batch = []
        if batch:
            yield batch


async def load_panel(archive, reference: DatasetRef, query: PanelQuery, *, max_cells=MAX_PANEL_CELLS):
    query = PanelQuery.model_validate(query.model_dump())
    if type(max_cells) is not int or not 0 < max_cells <= MAX_PANEL_CELLS:
        raise JobError("invalid_panel_budget", "观察读取预算无效", 422)
    manifest = await archive.read_dataset(reference)
    if set(query.fields) & KIND_FIELDS["financial"]:
        raise JobError("financial_alignment_required", "财务字段须绑定已验证日历和披露时点对齐；不能按报告行日期直接透视", 422)
    floor = date.fromisoformat(manifest["warmup_start"] if query.include_warmup else manifest["start"])
    ceiling = date.fromisoformat(manifest["end"])
    start, end = query.start or floor, query.end or ceiling
    if not floor <= start <= end <= ceiling:
        raise JobError("panel_period_mismatch", "读取区间超出固定版本的研究/暖机边界", 422)
    instruments = query.instruments or manifest["instruments"]
    if not set(instruments) <= set(manifest["instruments"]):
        raise JobError("panel_universe_mismatch", "读取证券不在固定股票池内；未静默替换", 422)
    instruments = tuple(sorted(instruments))
    selected = set(instruments)
    sources = await archive.source_rows([UUID(item["source_id"]) for item in manifest["sources"]])
    dates = set()
    for source, artifact in sources:
        page = archive.verified_source(source, artifact)
        if page.kind == "financial":
            continue
        for row in page.observations:
            if row.instrument_id in selected and start <= row.effective_date <= end:
                dates.add(row.effective_date.isoformat())
    if not dates:
        raise JobError("no_observed_dates", "选定范围无已观察日期；未用工作日或假行情补齐", 404)
    requested_cells = len(dates) * len(instruments) * len(query.fields)
    if requested_cells > max_cells:
        raise JobError("panel_budget_exceeded", "观察查询超过20万格或本次更低预算；须接列式分区/后台读取，未先分配完整面板", 422)
    cells = {}
    for source, artifact in sources:
        page = archive.verified_source(source, artifact)
        if page.kind == "financial":
            continue
        for row in page.observations:
            if row.instrument_id not in selected or not start <= row.effective_date <= end:
                continue
            values = cells.setdefault((row.effective_date.isoformat(), row.instrument_id), {})
            for field in query.fields:
                if field not in row.values:
                    continue
                value = row.values[field]
                if field in values and values[field] != value:
                    raise JobError("conflicting_observations", "固定来源同日同证券同字段冲突；未按采集时间覆盖或自动选择最新", 409)
                values[field] = value
    total = len(dates) * len(instruments)
    counts = {}
    for field in query.fields:
        present = [values[field] for values in cells.values() if field in values]
        nulls = sum(value is None for value in present)
        excluded = sum(value is not None and eligible_value(field, value) is None for value in present)
        counts[field] = {"observed": len(present), "valid": len(present) - nulls - excluded, "source_null": nulls, "absent": total - len(present), "excluded_domain": excluded}
    summary = {"method_version": METHOD_VERSION, "data_version": reference.data_version, "qualification": "observation_only", "date_basis": "observed_not_verified_calendar", "calendar_gaps": None, "requested_cells": requested_cells, "observed_dates": len(dates), "securities": len(instruments), "fields": counts}
    return ObservedPanel(tuple(sorted(dates)), instruments, tuple(query.fields), cells, query.batch_rows, summary)
