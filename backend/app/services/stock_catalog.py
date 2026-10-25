import re
from datetime import date, datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import StockInstrument


def normalize_stock_catalog(rows: list[dict], *, today: date) -> list[dict]:
    if not rows or len(rows) > 10000:
        raise ValueError("股票目录为空或超过10000条边界")
    normalized = []
    seen = set()
    for row in rows:
        identifier = row.get("ts_code")
        if not isinstance(identifier, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", identifier):
            raise ValueError("证券标准标识缺失或格式错误")
        code, suffix = identifier.split(".")
        expected = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[suffix]
        if row.get("exchange") != expected or row.get("symbol") != code:
            raise ValueError("证券代码与交易所冲突")
        if not ((suffix == "SH" and code.startswith("6")) or (suffix == "SZ" and code.startswith(("0", "3"))) or (suffix == "BJ" and code.startswith(("4", "8", "9")))):
            continue
        if code in seen:
            raise ValueError("股票目录存在重复证券")
        seen.add(code)
        name = row.get("name")
        industry = row.get("industry") or ""
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 32 or not isinstance(industry, str) or len(industry) > 32:
            raise ValueError("股票名称或行业字段无效")
        if row.get("list_status") != "L":
            raise ValueError("供应商返回非上市状态，目录查询契约冲突")
        raw_date = row.get("list_date")
        if not isinstance(raw_date, str) or not re.fullmatch(r"\d{8}", raw_date):
            raise ValueError("上市日期缺失或格式错误")
        listed = datetime.strptime(raw_date, "%Y%m%d").date()
        if listed > today:
            raise ValueError("上市日期晚于当前日期")
        normalized.append({"code": code, "name": name.strip(), "exchange": expected, "industry": industry.strip(), "list_date": listed})
    if not normalized:
        raise ValueError("目录没有可用A股证券")
    return sorted(normalized, key=lambda row: row["code"])


async def upsert_stock_catalog(session: AsyncSession, rows: list[dict]) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for offset in range(0, len(rows), 500):
        statement = insert(StockInstrument).values([{**row, "updated_at": now} for row in rows[offset:offset + 500]])
        await session.execute(statement.on_conflict_do_update(index_elements=[StockInstrument.code], set_={field: getattr(statement.excluded, field) for field in ("name", "exchange", "industry", "list_date", "updated_at")}))
