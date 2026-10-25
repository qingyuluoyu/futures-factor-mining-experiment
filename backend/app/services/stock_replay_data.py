"""验证原始日线并生成不可变、可追溯的回放快照。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock_replay import StockReplayDataset


class ReplayDataError(ValueError):
    def __init__(self, message: str, error_type: str = "invalid_data") -> None:
        super().__init__(message)
        self.error_type = error_type


def normalize_replay_instrument(code: str) -> tuple[str, str, str]:
    if not isinstance(code, str):
        raise ReplayDataError("invalid main-board instrument code")
    match = re.fullmatch(r"((?:60|00)\d{4})(?:\.(SH|SZ))?", code.strip().upper())
    if match is None:
        raise ReplayDataError("only 60xxxx/00xxxx main-board instrument codes are supported")
    normalized_code, suffix = match.groups()
    expected_suffix = "SH" if normalized_code.startswith("60") else "SZ"
    if suffix is not None and suffix != expected_suffix:
        raise ReplayDataError("instrument code and exchange conflict", "data_conflict")
    return normalized_code, f"{normalized_code}.{expected_suffix}", "SSE" if expected_suffix == "SH" else "SZSE"


def parse_replay_date(value: date | str, field: str = "date") -> date:
    if isinstance(value, datetime):
        raise ReplayDataError(f"{field}: expected date, not timestamp")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise ReplayDataError(f"{field}: invalid date")
    try:
        return datetime.strptime(value, "%Y%m%d" if len(value) == 8 else "%Y-%m-%d").date()
    except ValueError as exc:
        raise ReplayDataError(f"{field}: invalid date") from exc


def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ReplayDataError(f"{field}: missing numeric value", "missing_data")
    if len(str(value)) > 64:
        raise ReplayDataError(f"{field}: numeric representation is too long")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ReplayDataError(f"{field}: invalid numeric value") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero) or number > Decimal("1e14"):
        raise ReplayDataError(f"{field}: non-finite or out-of-range numeric value")
    if number.as_tuple().exponent < -12:
        raise ReplayDataError(f"{field}: unsupported numeric precision")
    return number


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _index_rows(rows: Sequence[Mapping], source: str, instrument_id: str) -> dict[date, Mapping]:
    if not rows:
        raise ReplayDataError(f"{source}: missing source rows", "missing_data")
    if len(rows) > 366:
        raise ReplayDataError(f"{source}: exceeds 366-row import boundary")
    indexed = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReplayDataError(f"{source}: invalid row")
        if row.get("ts_code") != instrument_id:
            raise ReplayDataError(f"{source}: instrument identifier missing or conflicting", "data_conflict")
        trade_date = parse_replay_date(row.get("trade_date"), f"{source}.trade_date")
        if trade_date in indexed:
            raise ReplayDataError(f"{source}: duplicate date {trade_date}", "data_conflict")
        indexed[trade_date] = row
    return indexed


def normalize_replay_dataset(
    code: str,
    name: str,
    daily_rows: Sequence[Mapping],
    factor_rows: Sequence[Mapping],
    limit_rows: Sequence[Mapping],
    calendar_rows: Sequence[Mapping],
    *,
    fetched_at: datetime | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> dict:
    code, instrument_id, exchange = normalize_replay_instrument(code)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 32:
        raise ReplayDataError("invalid instrument name")
    name = name.strip()
    if "ST" in name.upper() or "退" in name or name.upper().startswith(("N", "C")):
        raise ReplayDataError("only ordinary main-board A shares are supported; ST/new-listing/delisting excluded")
    daily = _index_rows(daily_rows, "daily", instrument_id)
    factors = _index_rows(factor_rows, "adj_factor", instrument_id)
    limits = _index_rows(limit_rows, "stk_limit", instrument_id)
    range_start = parse_replay_date(start_date, "start_date") if start_date is not None else min(daily)
    range_end = parse_replay_date(end_date, "end_date") if end_date is not None else max(daily)
    if range_start > range_end or (range_end - range_start).days + 1 > 366:
        raise ReplayDataError("date range must be ascending and at most 366 calendar days")
    if not calendar_rows:
        raise ReplayDataError("trade_cal: missing official calendar", "missing_data")
    if len(calendar_rows) > 366:
        raise ReplayDataError("trade_cal: exceeds 366-row import boundary")
    calendar_seen = set()
    open_dates = set()
    for row in calendar_rows:
        if not isinstance(row, Mapping) or row.get("exchange") != exchange:
            raise ReplayDataError("trade_cal: calendar exchange missing or conflicting", "data_conflict")
        calendar_date = parse_replay_date(row.get("cal_date"), "trade_cal.cal_date")
        if calendar_date in calendar_seen:
            raise ReplayDataError(f"trade_cal: duplicate calendar date {calendar_date}", "data_conflict")
        calendar_seen.add(calendar_date)
        is_open = row.get("is_open")
        if type(is_open) not in (int, str) or str(is_open) not in ("0", "1"):
            raise ReplayDataError("trade_cal: missing or invalid is_open", "missing_data")
        if str(is_open) == "1" and range_start <= calendar_date <= range_end:
            open_dates.add(calendar_date)
    required_calendar = {range_start + timedelta(days=offset) for offset in range((range_end - range_start).days + 1)}
    if not required_calendar.issubset(calendar_seen):
        raise ReplayDataError("trade_cal: missing complete official calendar coverage", "missing_data")
    if not open_dates:
        raise ReplayDataError("trade_cal: missing open calendar days", "missing_data")
    for source, indexed in (("daily", daily), ("adj_factor", factors), ("stk_limit", limits)):
        if set(indexed) != open_dates:
            raise ReplayDataError(f"{source}: missing or conflicting official calendar coverage", "missing_data")
    bars = []
    for trade_date in sorted(open_dates):
        row = daily[trade_date]
        prices = {field: _decimal(row.get(field), f"daily.{field}") for field in ("open", "high", "low", "close")}
        if not prices["low"] <= min(prices["open"], prices["close"]) <= max(prices["open"], prices["close"]) <= prices["high"]:
            raise ReplayDataError(f"daily: invalid OHLC ordering on {trade_date}")
        for field, price in prices.items():
            if price != price.quantize(Decimal("0.0001")):
                raise ReplayDataError(f"daily.{field}: unsupported price precision")
        volume_shares = _decimal(row.get("vol"), "daily.vol", allow_zero=True) * 100
        if volume_shares != volume_shares.to_integral_value() or volume_shares > 9007199254740991:
            raise ReplayDataError("daily.vol: volume must convert to integral shares within safe range")
        adj_factor = _decimal(factors[trade_date].get("adj_factor"), "adj_factor.adj_factor")
        up_limit = _decimal(limits[trade_date].get("up_limit"), "stk_limit.up_limit")
        down_limit = _decimal(limits[trade_date].get("down_limit"), "stk_limit.down_limit")
        if down_limit >= up_limit or prices["high"] > up_limit or prices["low"] < down_limit:
            raise ReplayDataError(f"stk_limit: prices conflict with official daily limits on {trade_date}", "data_conflict")
        bars.append({
            "trade_date": trade_date.isoformat(),
            **{field: _decimal_text(value) for field, value in prices.items()},
            "volume_shares": int(volume_shares), "adj_factor": _decimal_text(adj_factor),
            "up_limit": _decimal_text(up_limit), "down_limit": _decimal_text(down_limit),
        })
    fetched_at = fetched_at or datetime.now(timezone.utc)
    if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ReplayDataError("fetched_at must be a timezone-aware timestamp")
    meta = {
        "supplier": "daoshare", "market": "stock", "exchange": exchange,
        "instrument_id": instrument_id, "currency": "CNY", "timezone": "Asia/Shanghai",
        "frequency": "1d", "adjustment": "none", "price_unit": "CNY/share",
        "volume_unit": "share", "source_volume_unit": "lot_100_shares",
        "calendar_dates": [value.isoformat() for value in sorted(open_dates)],
        "source_apis": ["daily", "adj_factor", "stk_limit", "trade_cal"],
        "schema_version": 1, "normalization_version": "stock-replay-data-v1",
        "financial_statement_basis": "not_applicable",
        "availability_basis": "end_of_trading_day_snapshot; not point-in-time fundamental data",
        "authorization_boundary": "user-authorized internal replay; no redistribution or sublicensing",
        "corporate_actions": "factor changes must be rejected by replay execution",
    }
    stable = {
        "code": code, "instrument_id": instrument_id, "name": name,
        "start_date": min(open_dates).isoformat(), "end_date": max(open_dates).isoformat(),
        "bars_json": bars, "meta": meta,
    }
    version = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        **stable, "version": version, "start_date": min(open_dates), "end_date": max(open_dates),
        "meta": {**meta, "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(), "data_version": version},
    }


async def persist_replay_dataset(session: AsyncSession, snapshot: dict) -> tuple[StockReplayDataset, bool]:
    """在调用方事务内插入新版本；相同版本只读取既有快照。"""
    result = await session.execute(
        insert(StockReplayDataset).values(**snapshot)
        .on_conflict_do_nothing(index_elements=[StockReplayDataset.version])
        .returning(StockReplayDataset.id),
    )
    created = result.scalar_one_or_none() is not None
    dataset = (await session.execute(
        select(StockReplayDataset).where(StockReplayDataset.version == snapshot["version"]),
    )).scalar_one()
    return dataset, created
