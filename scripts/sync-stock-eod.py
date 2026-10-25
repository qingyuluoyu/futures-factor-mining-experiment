import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_eod_sync import TeajoinEodSource, run_eod
from scripts.migrate_stock_eod import migrate_stock_eod
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def run(args):
    settings = load_runtime_settings()
    if settings.postgres_host not in ("localhost", "127.0.0.1") or settings.postgres_port != 5433:
        raise ValueError("该手动校验入口仅允许专用本地5433数据库，部署环境请使用受控worker")
    cached = None
    if args.cached_daily:
        path = Path(args.cached_daily).resolve(strict=True)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("缓存超过8MiB边界")
        cached = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cached, dict) or cached.get("has_more") is not False:
            raise ValueError("缓存未确认完整分页")
    engine = create_replay_engine(settings)
    try:
        await migrate_stock_eod(engine, check=True)
        return await run_eod(async_sessionmaker(engine, expire_on_commit=False), TeajoinEodSource(settings.tushare_token), args.date, cached_daily=cached)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="受控盘后增量同步：每交易日持久化单次领取，最多3个上游请求，无自动付费重试")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--cached-daily", help="复用已获授权且具有has_more=false的daily响应，不重复下载行情")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"EOD sync failed ({type(error).__name__}); credentials and source payload omitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
