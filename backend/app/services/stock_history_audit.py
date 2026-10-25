from datetime import date, datetime

from sqlalchemy import String, and_, case, cast, distinct, func, not_, or_, select

from app.models.kline_history import KlineHistory
from app.models.stock import StockInstrument


async def audit_stock_history(connection, *, as_of: date, sample_limit: int = 50) -> dict:
    if type(sample_limit) is not int or not 1 <= sample_limit <= 50:
        raise ValueError("sample_limit must be an integer from 1 to 50")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError("as_of must be a date")
    history = KlineHistory.__table__
    catalog = StockInstrument.__table__
    duplicates = (select(history.c.code, history.c.trade_date, func.count().label("copies"))
        .where(history.c.market_type == "stock")
        .group_by(history.c.code, history.c.trade_date).having(func.count() > 1).subquery())
    joined = history.outerjoin(catalog, history.c.code == catalog.c.code).outerjoin(duplicates,
        and_(history.c.code == duplicates.c.code, history.c.trade_date == duplicates.c.trade_date))
    stripped_code = history.c.code
    for digit in "0123456789":
        stripped_code = func.replace(stripped_code, digit, "")
    prices = [history.c.open, history.c.high, history.c.low, history.c.close]
    invalid_price = or_(*[or_(price.is_(None), price <= 0,
        func.lower(cast(price, String)).in_(("nan", "infinity", "-infinity", "inf", "-inf"))) for price in prices])
    flags = {
        "invalid_code": or_(history.c.code.is_(None), func.length(history.c.code) != 6, stripped_code != ""),
        "future": history.c.trade_date > as_of,
        "invalid_price": invalid_price,
        "ohlc_inconsistent": and_(not_(invalid_price), or_(history.c.high < history.c.low,
            history.c.open > history.c.high, history.c.close > history.c.high,
            history.c.open < history.c.low, history.c.close < history.c.low)),
        "invalid_volume": or_(history.c.volume.is_(None), history.c.volume < 0,
            func.lower(cast(history.c.volume, String)).in_(("nan", "infinity", "-infinity", "inf", "-inf"))),
        "zero_volume": history.c.volume == 0,
        "missing_catalog": catalog.c.id.is_(None),
        "before_listing": and_(catalog.c.list_date.is_not(None), history.c.trade_date < catalog.c.list_date),
        "duplicate": duplicates.c.copies.is_not(None),
    }
    selected = history.c.market_type == "stock"
    risk = or_(*flags.values())
    counts = {name: func.coalesce(func.sum(case((expression, 1), else_=0)), 0).label(name)
        for name, expression in flags.items()}
    aggregation = (await connection.execute(select(
        func.count().label("rows"), func.count(distinct(history.c.code)).label("securities"),
        func.min(history.c.trade_date).label("earliest_trade_date"),
        func.max(history.c.trade_date).label("latest_trade_date"),
        select(func.count()).select_from(catalog).scalar_subquery().label("catalog_securities"),
        func.count(distinct(catalog.c.code)).label("catalog_securities_with_history"),
        func.coalesce(func.sum(case((risk, 1), else_=0)), 0).label("risk_rows"),
        *counts.values()).select_from(joined).where(selected))).mappings().one()
    duplicate_summary = (await connection.execute(select(func.count().label("groups"),
        func.coalesce(func.sum(duplicates.c.copies - 1), 0).label("extra_rows")).select_from(duplicates))).mappings().one()
    sample_rows = (await connection.execute(select(history.c.id, history.c.code, history.c.trade_date,
        *[case((expression, True), else_=False).label(name) for name, expression in flags.items()])
        .select_from(joined).where(selected, risk).order_by(history.c.code, history.c.trade_date, history.c.id)
        .limit(sample_limit))).mappings().all()
    quality = {f"{name}_rows": int(aggregation[name]) for name in flags if name != "duplicate"}
    quality.update(duplicate_groups=int(duplicate_summary["groups"]), duplicate_extra_rows=int(duplicate_summary["extra_rows"]))
    summary = {name: aggregation[name] for name in ("rows", "securities", "earliest_trade_date", "latest_trade_date", "catalog_securities", "catalog_securities_with_history")}
    summary["catalog_securities_without_history"] = summary["catalog_securities"] - summary["catalog_securities_with_history"]
    for name in ("earliest_trade_date", "latest_trade_date"):
        summary[name] = summary[name].isoformat() if summary[name] is not None else None
    samples = [{**dict(sample), "trade_date": sample["trade_date"].isoformat()} for sample in sample_rows]
    return {
        "audit_version": "stock-history-readonly-audit-v1", "as_of": as_of.isoformat(), "market_type": "stock",
        "status": "no_history" if not summary["rows"] else "audited_unverified",
        "summary": summary, "quality": quality, "risk_rows": int(aggregation["risk_rows"]),
        "samples": samples, "sample_limit": sample_limit, "samples_truncated": aggregation["risk_rows"] > len(samples),
        "evidence": {
            "verification": "unverified_legacy_provenance_and_adjustment_anchor", "unverified_rows": summary["rows"],
            "independent_verification": False, "provider": "not_recorded_per_legacy_batch",
            "price_adjustment": "legacy_importer_qfq_intent_anchor_not_recorded_per_row",
            "price_comparison": "not_performed_raw_eod_and_legacy_qfq_are_not_equivalent",
            "volume_unit": "lot_integer_legacy_storage", "decimal_lot_loss": "unverified_cannot_infer_from_integer_storage",
            "data_available_at": "not_recorded", "collection_time": "not_recorded", "data_version": "not_recorded",
            "catalog_scope": "current_catalog_absence_does_not_prove_invalid_historical_security",
            "zero_volume_scope": "requires_review_not_assumed_missing_or_suspended",
        },
        "source_calls": 0, "model_calls": 0, "production_data_modified": False,
    }
