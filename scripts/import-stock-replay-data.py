"""有界导入已授权供应商原始日线；只插入新的不可变回放快照。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.data.tushare_client import TushareClient
from app.services.stock_replay_data import (
    ReplayDataError, normalize_replay_dataset, normalize_replay_instrument,
    parse_replay_date, persist_replay_dataset,
)
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings, migrate_stock_replay


def validate_import_request(codes: str, start_date: date | str, end_date: date | str) -> tuple[list[str], date, date]:
    entries = codes.split(",")
    if not 1 <= len(entries) <= 20:
        raise ReplayDataError("import requires 1..20 codes")
    normalized = [normalize_replay_instrument(value)[0] for value in entries]
    if len(set(normalized)) != len(normalized):
        raise ReplayDataError("duplicate instrument codes")
    start = parse_replay_date(start_date, "start_date")
    end = parse_replay_date(end_date, "end_date")
    if start > end or (end - start).days + 1 > 366:
        raise ReplayDataError("date range must be ascending and at most 366 calendar days")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if end > now.date() or (end == now.date() and now.hour < 16):
        raise ReplayDataError("end_date must be a closed historical trading date")
    return normalized, start, end


def fetch_replay_snapshot(client: TushareClient, code: str, start_date: date, end_date: date) -> dict:
    code, instrument_id, exchange = normalize_replay_instrument(code)
    instruments = client.query(
        "stock_basic", {"ts_code": instrument_id}, fields="ts_code,name,market",
    )
    if len(instruments) != 1 or instruments[0].get("ts_code") != instrument_id:
        raise ReplayDataError("stock_basic: missing or conflicting instrument metadata", "missing_data")
    instrument = instruments[0]
    name = instrument.get("name")
    if (instrument.get("market") != "主板" or not isinstance(name, str) or
        "ST" in name.upper() or "退" in name or name.upper().startswith(("N", "C"))):
        raise ReplayDataError("only ordinary main-board A shares are supported; ST/new-listing/delisting excluded")
    params = {"ts_code": instrument_id, "start_date": start_date.strftime("%Y%m%d"), "end_date": end_date.strftime("%Y%m%d")}
    daily = client.query("daily", params, fields="ts_code,trade_date,open,high,low,close,vol")
    factors = client.query("adj_factor", params, fields="ts_code,trade_date,adj_factor")
    limits = client.query("stk_limit", params, fields="ts_code,trade_date,up_limit,down_limit")
    calendar = client.query("trade_cal", {
        "exchange": exchange, "start_date": params["start_date"], "end_date": params["end_date"],
    }, fields="exchange,cal_date,is_open")
    return normalize_replay_dataset(
        code, name, daily, factors, limits, calendar, start_date=start_date, end_date=end_date,
    )


async def import_snapshots(codes: list[str], start_date: date, end_date: date) -> list[dict]:
    settings = load_runtime_settings()
    engine = create_replay_engine(settings)
    client = None
    try:
        await migrate_stock_replay(engine, check=True)
        client = TushareClient(settings.tushare_token, timeout=15.0, max_retries=1)
        snapshots = []
        for code in codes:
            try:
                snapshots.append(await asyncio.to_thread(fetch_replay_snapshot, client, code, start_date, end_date))
            except ReplayDataError:
                raise
            except Exception as exc:
                raise ReplayDataError(f"source request failed for {code} ({type(exc).__name__})", "source_failure") from None
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        imported = []
        async with session_factory() as session, session.begin():
            for snapshot in snapshots:
                dataset, created = await persist_replay_dataset(session, snapshot)
                imported.append({
                    "id": str(dataset.id), "code": dataset.code, "version": dataset.version,
                    "start_date": dataset.start_date.isoformat(), "end_date": dataset.end_date.isoformat(),
                    "bars": len(dataset.bars_json), "created": created,
                })
        return imported
    finally:
        if client is not None:
            client.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", required=True, help="1至20个沪深主板代码，以逗号分隔")
    parser.add_argument("--start-date", required=True, help="YYYYMMDD，含当日")
    parser.add_argument("--end-date", required=True, help="YYYYMMDD，含当日；最多366自然日")
    args = parser.parse_args()
    try:
        codes, start_date, end_date = validate_import_request(args.codes, args.start_date, args.end_date)
        imported = asyncio.run(import_snapshots(codes, start_date, end_date))
        print(json.dumps({"datasets": imported}, ensure_ascii=False))
    except ReplayDataError as exc:
        print(f"stock replay import failed [{exc.error_type}]: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"stock replay import failed ({type(exc).__name__}); no partial snapshots committed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
