"""Deterministic selection of local financial disclosures before panel alignment."""

from __future__ import annotations

from datetime import date
from itertools import groupby
import math
import re
from typing import Iterable

FINANCIAL_SELECTION_VERSION = "latest-disclosed-report-frontier-v1"


class FinancialSelectionError(ValueError):
    pass


def select_financial_rows(rows: Iterable[tuple]) -> list[tuple]:
    """Select the newest disclosed report frontier; unknown publish dates are unusable."""
    disclosed = []
    observed = set()
    for source_row in rows:
        row = tuple(source_row)
        if len(row) != 7:
            raise FinancialSelectionError("本地财务数据字段数量错误")
        code, report_date, publish_date, *values = row
        if publish_date is None:
            continue
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise FinancialSelectionError("本地股票证券标识必须为六位数字代码")
        if type(report_date) is not date or type(publish_date) is not date:
            raise FinancialSelectionError("本地股票数据日期缺失或类型错误")
        if report_date > publish_date:
            raise FinancialSelectionError("财报报告期晚于公告日期")
        key = (code, report_date)
        if key in observed:
            raise FinancialSelectionError("本地股票数据存在重复或冲突记录")
        observed.add(key)
        for value in values:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)):
                raise FinancialSelectionError("本地股票数据包含非有限数值或错误类型")
        disclosed.append(row)
    disclosed.sort(key=lambda row: (row[0], row[2], row[1]))
    selected = []
    frontier = {}
    for (code, publish_date), disclosures in groupby(disclosed, key=lambda row: (row[0], row[2])):
        latest = max(disclosures, key=lambda row: row[1])
        if code not in frontier or latest[1] >= frontier[code]:
            frontier[code] = latest[1]
            selected.append(latest)
    return selected
