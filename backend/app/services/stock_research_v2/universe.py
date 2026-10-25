from datetime import date, datetime

from pydantic import Field, field_validator, model_validator

from app.schemas.stock_research_v2 import Contract, SHA256, equity_identifier, explicit_date

METHOD_VERSION = "stock-historical-universe-v1"


class UniverseMembership(Contract):
    instrument_id: str
    valid_from: date
    valid_to: date | None
    known_on: date
    exit_known_on: date | None = None
    source_sha256: str = Field(pattern=SHA256)

    _identity = field_validator("instrument_id")(equity_identifier)
    _explicit_dates = field_validator("valid_from", "valid_to", "known_on", "exit_known_on", mode="before")(explicit_date)

    @model_validator(mode="after")
    def interval(self):
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("成员有效区间必须为左闭右开非空区间")
        if self.valid_to is not None and (self.exit_known_on is None or self.exit_known_on > self.valid_to):
            raise ValueError("退出缺少当时已知证据，不能回填历史退出日期")
        if self.valid_to is None and self.exit_known_on is not None:
            raise ValueError("未退出成员不能携带无有效日期的退出证据")
        return self


class HistoricalUniverse(Contract):
    pool_id: str = Field(min_length=1, max_length=100)
    pool_version: str = Field(pattern=SHA256)
    start: date
    end: date
    members: list[UniverseMembership] = Field(min_length=1, max_length=100000)
    _explicit_dates = field_validator("start", "end", mode="before")(explicit_date)

    @model_validator(mode="after")
    def timeline(self):
        if self.start > self.end:
            raise ValueError("股票池覆盖日期倒置")
        intervals = {}
        for member in sorted(self.members, key=lambda value: (value.instrument_id, value.valid_from)):
            previous = intervals.get(member.instrument_id)
            if previous is not None and (previous.valid_to is None or previous.valid_to > member.valid_from):
                raise ValueError("同证券成员区间重叠；不能静默取最新名单")
            intervals[member.instrument_id] = member
        return self


def universe_at(dataset: HistoricalUniverse, decision_on: date, *, expected_version: str | None = None) -> list[str]:
    if not isinstance(dataset, HistoricalUniverse) or type(decision_on) is not date or isinstance(decision_on, datetime):
        raise ValueError("历史股票池或决策日期类型无效")
    if expected_version is not None and expected_version != dataset.pool_version:
        raise ValueError("历史股票池版本不匹配")
    if not dataset.start <= decision_on <= dataset.end:
        raise ValueError("日期超出历史股票池的真实覆盖范围")
    return sorted(member.instrument_id for member in dataset.members if member.valid_from <= decision_on
        and member.known_on <= decision_on and (member.valid_to is None or decision_on < member.valid_to))
