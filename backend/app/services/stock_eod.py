import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import cast, func, select, true
from sqlalchemy.dialects.postgresql import JSONPATH

from app.models.stock_eod import StockEodBatch


METHOD_VERSION = "stock-eod-2026-10-18.v1"
MAX_ROWS = 10000
SOURCE_URI = "https://teajoin.com/debug/api/daily"


def content_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=lambda item: item.isoformat()).encode()).hexdigest()


def normalize_eod(rows: list[dict], catalog: list[dict], trade_date: date, *, acquired_at: datetime, has_more: bool | None, source_evidence: dict | None = None) -> dict:
    if has_more is not False or not rows or len(rows) > MAX_ROWS or len(catalog) > MAX_ROWS:
        raise ValueError("供应商未确认完整分页、无行情或超过10000条边界")
    if type(trade_date) is not date or acquired_at.tzinfo is None:
        raise ValueError("快照日期或采集时区无效")
    indexed = {item["code"]: item for item in catalog if item.get("list_date") is None or item["list_date"] <= trade_date}
    pre_listing = {item["code"] for item in catalog if item.get("list_date") is not None and item["list_date"] > trade_date}
    quotes, seen = [], set()
    for row in rows:
        identifier = row.get("ts_code", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", identifier):
            raise ValueError("盘后证券标识无效")
        code, suffix = identifier.split(".")
        if code in pre_listing:
            raise ValueError("盘后来源包含上市日前行情")
        if not ((suffix == "SH" and code.startswith("6")) or (suffix == "SZ" and code.startswith(("0", "3"))) or (suffix == "BJ" and code.startswith(("4", "8", "9")))):
            raise ValueError("盘后来源包含非A股证券")
        exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[suffix]
        if code in seen or (code in indexed and indexed[code]["exchange"] != exchange):
            raise ValueError("盘后证券重复或交易所冲突")
        if str(row.get("trade_date")) != trade_date.strftime("%Y%m%d"):
            raise ValueError("盘后行情交易日冲突")
        values = {}
        for field in ("open", "high", "low", "close", "pre_close", "vol", "amount"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (field not in ("vol", "amount") and value == 0):
                raise ValueError("盘后价量或单位字段无效")
            values[field] = float(value)
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError("盘后OHLC关系无效")
        seen.add(code)
        quotes.append({"code": code, "instrument_id": identifier, "trade_date": trade_date.isoformat(),
            **{name: values[name] for name in ("open", "high", "low", "close", "pre_close")},
            "volume": values["vol"], "amount": float(Decimal(str(row["amount"])) * 1000),
            "change_pct": round((values["close"] / values["pre_close"] - 1) * 100, 6)})
    quotes.sort(key=lambda item: item["code"])
    canonical_catalog = sorted((dict(item) for item in indexed.values()), key=lambda item: item["code"])
    snapshot = {"method_version": METHOD_VERSION, "provider": "user_authorized_teajoin_tushare_compatible", "source_uri": SOURCE_URI,
        "trade_date": trade_date.isoformat(), "market": "CN_A", "currency": "CNY", "timezone": "Asia/Shanghai", "frequency": "daily",
        "price_adjustment": "none", "volume_unit": "lot_100_shares", "amount_unit": "CNY", "reference_price": "vendor_ex_rights_pre_close",
        "license_scope": "operator_configured_account_not_redistribution_certified", "vendor_has_more": False,
        "catalog_version": content_hash(canonical_catalog), "quotes": quotes, "source_row_count": len(quotes), "expected_count": len(indexed),
        "covered_count": len(seen.intersection(indexed)), "missing_codes": sorted(set(indexed) - seen), "unknown_codes": sorted(seen - set(indexed))}
    if source_evidence is not None:
        snapshot["source_evidence"] = json.loads(json.dumps(source_evidence, allow_nan=False))
    snapshot["data_version"] = content_hash(snapshot)
    snapshot["collected_at"] = acquired_at.astimezone(timezone.utc).isoformat()
    return snapshot


def market_from_eod(snapshot: dict, instruments: list[dict]) -> dict:
    from app.services.stock_market import build_market_snapshot

    expected_version = snapshot.get("data_version")
    canonical = {key: value for key, value in snapshot.items() if key not in ("data_version", "collected_at")}
    if expected_version != content_hash(canonical) or snapshot.get("method_version") != METHOD_VERSION:
        raise ValueError("盘后快照版本或内容校验失败")
    trading_date = date.fromisoformat(snapshot["trade_date"])
    unknown = set(snapshot["unknown_codes"])
    bars = [{**quote, "trade_date": trading_date} for quote in snapshot["quotes"] if quote["code"] not in unknown]
    result = build_market_snapshot(instruments, bars, trading_date, None, reference_closes={quote["code"]: quote["pre_close"] for quote in snapshot["quotes"] if quote["code"] not in unknown})
    result.update(price_adjustment="none", source="teajoin daily 盘后快照", method_version=METHOD_VERSION,
        data_version=snapshot["data_version"], collected_at=snapshot["collected_at"], reference_price=snapshot["reference_price"],
        source_row_count=snapshot["source_row_count"], missing_codes=snapshot["missing_codes"], unknown_codes=snapshot["unknown_codes"],
        license_scope=snapshot["license_scope"])
    return result


async def latest_eod(session, today: date) -> StockEodBatch | None:
    return await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date <= today, StockEodBatch.status.in_(("completed", "partially_completed"))).order_by(StockEodBatch.trade_date.desc()).limit(1))


def eod_bar_statement(code: str, today: date, limit: int, dialect: str):
    if not re.fullmatch(r"\d{6}", code) or not 1 <= limit <= 3000:
        raise ValueError("盘后K线证券或数量无效")
    if dialect == "postgresql":
        quote = func.jsonb_path_query_first(StockEodBatch.snapshot_json, cast(f'$.quotes[*] ? (@.code == "{code}")', JSONPATH))
        statement = select(quote).where(quote.is_not(None), ~func.jsonb_exists(StockEodBatch.snapshot_json["unknown_codes"], code))
    else:
        quotes = func.json_each(StockEodBatch.snapshot_json, "$.quotes").table_valued("value").alias("quote")
        unknown = func.json_each(StockEodBatch.snapshot_json, "$.unknown_codes").table_valued("value").alias("unknown_code")
        statement = select(quotes.c.value).select_from(StockEodBatch).join(quotes, true()).where(func.json_extract(quotes.c.value, "$.code") == code,
            ~select(unknown.c.value).where(unknown.c.value == code).correlate(StockEodBatch).exists())
    return statement.where(StockEodBatch.trade_date <= today, StockEodBatch.status.in_(("completed", "partially_completed"))).order_by(StockEodBatch.trade_date.desc()).limit(min(limit, 250) + 1)


async def eod_bars(session, code: str, today: date, limit: int, *, include_reference: bool = False) -> list[dict]:
    quotes = (await session.execute(eod_bar_statement(code, today, limit, session.get_bind().dialect.name))).scalars().all()
    result = []
    for quote in reversed(quotes):
        if isinstance(quote, str):
            quote = json.loads(quote)
        bar = {"time": quote["trade_date"], **{field: quote[field] for field in ("open", "high", "low", "close", "volume")}}
        if include_reference:
            reference = quote.get("pre_close")
            if type(reference) not in (int, float) or not math.isfinite(reference) or reference <= 0:
                raise ValueError("盘后日线参考价无效，不能用相邻收盘替代供应商除权昨收")
            bar["pre_close"] = reference
        result.append(bar)
    return result


async def eod_coverage(session, code: str, today: date) -> tuple[int, str | None, str | None]:
    """Count only stored batches eligible for the same security-specific EOD read.

    This describes local storage, never the supplier's history or calendar completeness.
    Aggregate in the database; do not load full-market snapshot JSON into the API.
    """
    eligible = (eod_bar_statement(code, today, 1, session.get_bind().dialect.name)
        .add_columns(StockEodBatch.trade_date.label("coverage_date"))
        .order_by(None).limit(None).subquery())
    count, first, last = (await session.execute(select(
        func.count(), func.min(eligible.c.coverage_date), func.max(eligible.c.coverage_date)
    ).select_from(eligible))).one()
    return count, first.isoformat() if first else None, last.isoformat() if last else None
