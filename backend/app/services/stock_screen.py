import hashlib
import json

from app.schemas.stock_screen import StockScreenFilters
from app.services.stock_market import filter_members, finite_number


class StockScreenChangedError(ValueError):
    pass


def screen_snapshot(snapshot: dict, filters: StockScreenFilters) -> dict:
    rows = filter_members(snapshot["items"], industry=filters.industry, keyword=filters.keyword, sort=filters.sort, direction=filters.direction, data_only=filters.data_only)
    conditions = (("close", filters.min_close, filters.max_close), ("change_pct", filters.min_change_pct, filters.max_change_pct), ("volume", filters.min_volume, None))
    for field, lower, upper in conditions:
        if lower is None and upper is None:
            continue
        selected = []
        for row in rows:
            value = finite_number(row.get(field))
            if row.get("data_available") is True and value is not None and (lower is None or value >= lower) and (upper is None or value <= upper):
                selected.append(row)
        rows = selected
    metadata = {key: value for key, value in snapshot.items() if key not in ("items", "boards")}
    fingerprint = hashlib.sha256(json.dumps({"version": "stock-screen-v1", "metadata": metadata, "filters": filters.model_dump(exclude={"limit", "offset", "expected_fingerprint"}), "items": rows}, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    if filters.expected_fingerprint is not None and filters.expected_fingerprint != fingerprint:
        raise StockScreenChangedError("筛选快照已变化，请重新应用条件返回第一页；未混合新旧行情")
    return {
        **metadata,
        "screen_version": "stock-screen-v1",
        "screen_fingerprint": fingerprint,
        "applied_filters": filters.model_dump(),
        "filter_units": {"price": "CNY_per_share", "change_pct": "percent", "volume": snapshot["volume_unit"]},
        "items": rows[filters.offset:filters.offset + filters.limit],
        "total": len(rows),
        "has_more": filters.offset + filters.limit < len(rows),
    }
