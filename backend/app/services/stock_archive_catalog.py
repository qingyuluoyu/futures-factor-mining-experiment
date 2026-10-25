from datetime import date, datetime
import re
from uuid import UUID

from sqlalchemy import String, cast, func, literal, select, true
from sqlalchemy.dialects.postgresql import aggregate_order_by, array

from app.models.stock_daily_archive import StockDailyArchiveBatch
from app.schemas.stock_archive_catalog import ArchiveCatalogQuery
from app.services.stock_daily_archive import FINANCIAL_METADATA, METHOD_VERSION, SHANGHAI
from app.services.stock_eod import content_hash

FIELDS = ("id", "version", "source_data_version", "method_version", "trade_date", "status", "source_collected_at", "archived_at", "row_count", "expected_count", "missing_count", "unknown_count")
METADATA = (*FINANCIAL_METADATA, "catalog_version", "source_row_count", "verification", "data_available_at", "number_encoding")


class ArchiveCatalogChanged(ValueError):
    pass


def aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("归档目录时间缺少时区")
    return value


def catalog_item(row: dict, *, snapshot_at: datetime) -> dict:
    metadata = row["source_metadata"]
    collected, archived = aware(row["source_collected_at"]), aware(row["archived_at"])
    day = row["trade_date"]
    if (not isinstance(row["id"], UUID) or type(day) is not date or row["status"] != "completed"
            or not collected <= archived <= aware(snapshot_at) or day > collected.astimezone(SHANGHAI).date()
            or (day == collected.astimezone(SHANGHAI).date() and (collected.astimezone(SHANGHAI).hour, collected.astimezone(SHANGHAI).minute) < (16, 30))):
        raise ValueError("归档目录时点或状态不一致")
    if (row["method_version"] != METHOD_VERSION or not re.fullmatch(r"[a-f0-9]{64}", row["source_data_version"])
            or row["version"] != content_hash({"method_version": METHOD_VERSION, "source_data_version": row["source_data_version"]})):
        raise ValueError("归档目录版本不一致")
    if any(type(row[name]) is not int or not 0 <= row[name] <= 10000 for name in ("row_count", "expected_count", "missing_count", "unknown_count")) or row["row_count"] < 1 or row["row_count"] + row["missing_count"] != row["expected_count"]:
        raise ValueError("归档目录覆盖分区不一致")
    if (not isinstance(metadata, dict) or not set(METADATA) <= metadata.keys() or any(metadata.get(name) != value for name, value in FINANCIAL_METADATA.items())
            or not isinstance(metadata.get("catalog_version"), str) or not re.fullmatch(r"[a-f0-9]{64}", metadata["catalog_version"])
            or type(metadata.get("source_row_count")) is not int or metadata["source_row_count"] != row["row_count"] + row["unknown_count"] or metadata["source_row_count"] > 10000
            or metadata.get("verification") != "snapshot_only_not_independently_certified" or metadata.get("data_available_at") is not None
            or metadata.get("number_encoding") != "decimal_of_stored_json_number_not_original_response_text"):
        raise ValueError("归档目录来源或金融口径不一致")
    result = {name: row[name] for name in FIELDS if name != "status"}
    result["batch_id"] = str(result.pop("id"))
    for name in ("trade_date", "source_collected_at", "archived_at"):
        result[name] = result[name].isoformat()
    return {**result, **FINANCIAL_METADATA, "catalog_version": metadata["catalog_version"], "source_row_count": metadata["source_row_count"],
        "verification": "metadata_only_not_row_or_PIT_certification", "research_ready": False, "replay_ready": False}


async def read_archive_catalog(session, query: ArchiveCatalogQuery, *, now: datetime) -> dict:
    cutoff = query.snapshot_at or now
    if aware(cutoff) > aware(now) or (query.trade_date is not None and query.trade_date > cutoff.astimezone(SHANGHAI).date()):
        raise ValueError("目录筛选或快照不能晚于读取时点")
    batch = StockDailyArchiveBatch
    scoped = select(*(getattr(batch, name) for name in FIELDS), *(batch.source_metadata[name].label("metadata_" + name) for name in METADATA),
        func.jsonb_exists_all(batch.source_metadata, array(list(METADATA))).label("metadata_complete")).where(batch.archived_at <= cutoff)
    if query.trade_date is not None:
        scoped = scoped.where(batch.trade_date == query.trade_date)
    scoped = scoped.cte("archive_catalog_scope")
    membership = func.md5(func.coalesce(func.string_agg(cast(scoped.c.id, String), aggregate_order_by(literal(""), scoped.c.id)), ""))
    count = select(func.count().label("total"), membership.label("membership_token")).select_from(scoped).cte("archive_catalog_count")
    page = select(scoped).order_by(scoped.c.trade_date.desc(), scoped.c.archived_at.desc(), scoped.c.id.desc()).offset(query.offset).limit(query.limit).cte("archive_catalog_page")
    statement = select(count.c.total, count.c.membership_token, page).select_from(count).outerjoin(page, true()).order_by(page.c.trade_date.desc(), page.c.archived_at.desc(), page.c.id.desc())
    rows = (await session.execute(statement)).mappings().all()
    token = rows[0]["membership_token"]
    if query.membership_token is not None and token != query.membership_token:
        raise ArchiveCatalogChanged("归档目录成员已变化，请显式刷新重新锚定；未复用变化后的分页")
    if any(row["metadata_complete"] is not True for row in rows if row["id"] is not None):
        raise ValueError("归档目录来源字段缺失")
    items = [catalog_item({**row, "source_metadata": {name: row["metadata_" + name] for name in METADATA}}, snapshot_at=cutoff) for row in rows if row["id"] is not None]
    total = rows[0]["total"]
    return {"contract_version": "stock-archive-catalog-v1", "snapshot_at": cutoff.isoformat(), "membership_token": token, "items": items, "total": total, "has_more": query.offset + len(items) < total}
