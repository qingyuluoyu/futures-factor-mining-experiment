"""只更新授权股票元数据目录，不下载或覆盖历史行情、财务及账户。"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.data.tushare_client import TushareClient
from app.models.stock import StockInstrument
from app.services.stock_catalog import normalize_stock_catalog, upsert_stock_catalog
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def run(dry_run: bool) -> dict:
    settings = load_runtime_settings()
    client = TushareClient(settings.tushare_token, timeout=20, max_retries=1)
    try:
        page = await asyncio.to_thread(client.query_page, "stock_basic", {"list_status": "L"}, fields="ts_code,symbol,name,exchange,industry,list_date,list_status")
    finally:
        client.close()
    if page.has_more is True:
        raise ValueError("供应商返回 has_more=true，目录响应已截断，拒绝导入部分结果")
    records = page.records
    rows = normalize_stock_catalog(records, today=datetime.now(ZoneInfo("Asia/Shanghai")).date())
    if len(rows) < 1000 or len(records) == 6000:
        raise ValueError("目录数量异常或达到供应商常见上限，拒绝导入疑似截断结果")
    version = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    result = {
        "supplier": "daoshare", "dataset": "stock_basic",
        "scope": "returned_currently_listed_A_share_records",
        "supplier_has_more": page.has_more,
        "completeness": "supplier_reported_complete" if page.has_more is False else "supplier_not_certified",
        "completeness_note": "供应商声明没有后续数据；未做独立全市场完整性核验" if page.has_more is False else "供应商未提供完整性元数据，目录完整性未认证",
        "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "records": len(rows), "industries": len({row["industry"] for row in rows}),
        "version": version, "dry_run": dry_run, "history_downloaded": False,
    }
    if dry_run:
        return result
    engine = create_replay_engine(settings)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            result["catalog_before"] = await session.scalar(select(func.count()).select_from(StockInstrument))
            await upsert_stock_catalog(session, rows)
            result["catalog_after"] = await session.scalar(select(func.count()).select_from(StockInstrument))
    finally:
        await engine.dispose()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(arguments.dry_run)), ensure_ascii=False))
    except Exception as error:
        print(f"股票目录导入失败（{type(error).__name__}），未提交部分数据", file=sys.stderr)
        raise SystemExit(1) from None
