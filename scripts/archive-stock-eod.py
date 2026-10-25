import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.models.stock_eod import StockEodBatch
from app.services.stock_daily_archive import archive_eod, prepare_archive
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


def expected_version(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise argparse.ArgumentTypeError("Expected SHA256 data version")
    return value


def parser():
    result = argparse.ArgumentParser(description="仅归档已存EOD到独立股票Decimal影子表；默认只读dry-run，0上游/0模型，不修改旧历史读路径")
    result.add_argument("--batch-id", required=True, type=UUID)
    result.add_argument("--expected-data-version", required=True, type=expected_version)
    result.add_argument("--apply", action="store_true")
    return result


async def run(args):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise ValueError("仅允许专用本地PG5433")
    engine = create_replay_engine(settings)
    try:
        async with asyncio.timeout(30):
            if args.apply:
                from scripts.migrate_stock_daily_archive import migrate_stock_daily_archive
                await migrate_stock_daily_archive(engine, check=True)
            async with async_sessionmaker(engine, expire_on_commit=False)() as session, session.begin():
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" + ("" if args.apply else ", READ ONLY")))
                await session.execute(text("SET LOCAL statement_timeout = '5s'"))
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                if not args.apply and await session.scalar(text("SHOW transaction_read_only")) != "on":
                    raise RuntimeError("Dry-run requires read-only transaction")
                statement = select(StockEodBatch).where(StockEodBatch.id == args.batch_id).limit(1)
                source = await session.scalar(statement.with_for_update(read=True) if args.apply else statement)
                if source is None or source.data_version != args.expected_data_version:
                    raise ValueError("来源批次不存在或预期数据版本不匹配")
                now = datetime.now(ZoneInfo("Asia/Shanghai"))
                prepared = prepare_archive(source, now=now)
                batch = prepared["batch"]
                result = await archive_eod(session, source, now=now) if args.apply else {
                    "status": "dry_run", "version": batch["version"], "row_count": batch["row_count"]}
                result.update(source_batch_id=str(source.id), source_data_version=source.data_version,
                    trade_date=source.trade_date.isoformat(), expected_count=batch["expected_count"], missing_count=batch["missing_count"],
                    unknown_count=batch["unknown_count"], source_calls=0, model_calls=0, user_read_path_changed=False,
                    verification="snapshot_only_not_independently_certified", available_at_inferred=False,
                    number_encoding=batch["source_metadata"]["number_encoding"])
            return result
    finally:
        await engine.dispose()


def main() -> int:
    args = parser().parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=True, allow_nan=False))
        return 0
    except Exception as error:
        print(f"Stock archive failed ({type(error).__name__}); no partial transaction committed; credentials omitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
