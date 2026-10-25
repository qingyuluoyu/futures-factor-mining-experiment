from bisect import bisect_right
from datetime import date, datetime, time
import hashlib
import json
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, KIND_FIELDS, SHA256, SourcePage, SourceRef, aware_utc, equity_identifier, explicit_date, explicit_datetime, precise_values
from typing import Literal

METHOD_VERSION = "stock-disclosure-asof-v1"
DATE_ONLY_POLICY = "strict-next-session-0930-shanghai-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class TradingCalendar(Contract):
    start: date
    end: date
    sessions: list[date] = Field(min_length=1, max_length=20000)
    version: str = Field(pattern=SHA256)
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    _explicit_dates = field_validator("start", "end", mode="before")(explicit_date)

    @field_validator("sessions", mode="before")
    @classmethod
    def explicit_sessions(cls, values):
        if not isinstance(values, list) or len(values) > 20000:
            raise ValueError("交易日列表超出预算或类型无效")
        return [explicit_date(value) for value in values]

    @model_validator(mode="after")
    def coverage(self):
        if self.start > self.end or self.sessions != sorted(set(self.sessions)) or any(not self.start <= value <= self.end for value in self.sessions):
            raise ValueError("交易日历覆盖、顺序或日期重复无效")
        return self

    def next_session(self, announced_on):
        if not self.start <= announced_on <= self.end:
            raise ValueError("公告日期不在已验证日历覆盖内")
        index = bisect_right(self.sessions, announced_on)
        if index == len(self.sessions):
            raise ValueError("日历缺少公告后下一交易日，不能猜测工作日")
        return datetime.combine(self.sessions[index], time(9, 30), SHANGHAI)


class Disclosure(Contract):
    instrument_id: str
    report_period: date
    financial_basis: Literal["consolidated", "parent"]
    announced_at: datetime | None = None
    announced_on: date | None = None
    available_at: datetime | None = None
    collected_at: datetime
    vintage: Literal["original_filing", "revision_filing", "unverified"]
    vintage_reference: str | None = Field(default=None, min_length=1, max_length=200)
    source_sha256: str = Field(pattern=SHA256)
    values: dict[str, str | None] = Field(min_length=1, max_length=4)

    _identity = field_validator("instrument_id")(equity_identifier)
    _values = field_validator("values", mode="before")(precise_values)
    _explicit_times = field_validator("announced_at", "available_at", "collected_at", mode="before")(explicit_datetime)
    _explicit_dates = field_validator("report_period", "announced_on", mode="before")(explicit_date)

    @field_validator("announced_at", "available_at", "collected_at")
    @classmethod
    def timestamps(cls, value):
        return aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def chronology(self):
        if (self.announced_at is None) == (self.announced_on is None):
            raise ValueError("必须明确一种公告时刻精度")
        if self.vintage != "unverified" and self.vintage_reference is None:
            raise ValueError("原始披露/修订缺少具体证据引用")
        announced = self.announced_at.astimezone(SHANGHAI).date() if self.announced_at is not None else self.announced_on
        if self.report_period > announced or announced > self.collected_at.astimezone(SHANGHAI).date():
            raise ValueError("报告期/公告/采集日期顺序冲突")
        if self.announced_at is not None and self.collected_at < self.announced_at:
            raise ValueError("采集时间早于公告时刻")
        if self.available_at is not None and (self.available_at > self.collected_at or (self.announced_at is not None and self.available_at < self.announced_at) or self.available_at.astimezone(SHANGHAI).date() < announced):
            raise ValueError("数据可得时间与原始披露不一致")
        if not set(self.values) <= KIND_FIELDS["financial"]:
            raise ValueError("非支持的财务指标字段")
        return self


def select_asof(records: list[Disclosure], decision_at: datetime, *, calendar: TradingCalendar | None = None, financial_basis: Literal["consolidated", "parent"] = "consolidated") -> list[Disclosure]:
    if financial_basis not in ("consolidated", "parent"):
        raise ValueError("财报口径无效")
    decision = aware_utc(decision_at)
    if not isinstance(records, list) or len(records) > 100000:
        raise ValueError("披露记录超过单批预算")
    candidates = {}
    for row in records:
        if not isinstance(row, Disclosure):
            raise ValueError("披露记录必须经过类型契约验证")
        if row.financial_basis != financial_basis:
            continue
        announced = row.announced_at if row.announced_at is not None else datetime.combine(row.announced_on, time.min, SHANGHAI)
        if announced > decision:
            continue
        if row.announced_at is None:
            if calendar is None:
                raise ValueError("仅公告日期必须提供真实交易日历")
            available = calendar.next_session(row.announced_on)
            if row.available_at is not None:
                available = max(available, row.available_at)
        else:
            if row.available_at is None:
                raise ValueError("精确披露缺少已核实可得时间")
            available = row.available_at
        if available > decision:
            continue
        if row.vintage == "unverified":
            raise ValueError("缺少原始披露/修订版本证据，不可将当前修订值回填历史")
        key = (row.instrument_id, row.financial_basis)
        order = (row.report_period, available)
        existing = candidates.get(key)
        if existing is None or order > existing[0]:
            candidates[key] = (order, row, False)
        elif order == existing[0]:
            previous = existing[1]
            conflict = existing[2] or previous.values != row.values
            chosen = row if row.source_sha256 < previous.source_sha256 else previous
            candidates[key] = (order, chosen, conflict)
    if any(value[2] for value in candidates.values()):
        raise ValueError("同报告/可得时刻存在冲突披露，不能按输入顺序选取")
    return [candidates[key][1] for key in sorted(candidates)]


def disclosures_from_source(page: SourcePage, reference: SourceRef) -> list[Disclosure]:
    page = SourcePage.model_validate(page.model_dump())
    reference = SourceRef.model_validate(reference.model_dump())
    if page.kind != "financial" or reference.kind != "financial" or page.financial_basis not in ("consolidated", "parent"):
        raise ValueError("只允许明确合并/母公司口径的财务来源进入披露对齐")
    data = json.dumps(page.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf8")
    if len(data) != reference.artifact.byte_size or hashlib.sha256(data).hexdigest() != reference.artifact.sha256:
        raise ValueError("披露来源指纹与固定产物不一致")
    return [Disclosure(instrument_id=row.instrument_id, report_period=row.report_period, financial_basis=page.financial_basis,
        announced_at=row.announced_at, announced_on=row.announced_on, available_at=row.available_at, collected_at=page.collected_at,
        vintage=row.vintage, vintage_reference=row.vintage_reference, source_sha256=reference.artifact.sha256, values=row.values) for row in page.observations]
