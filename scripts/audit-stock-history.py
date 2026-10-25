import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.stock_history_audit import audit_stock_history
from scripts.migrate_stock_replay import create_replay_engine, load_runtime_settings


async def run(args):
    destination = Path(args.output).resolve()
    if destination.exists():
        raise FileExistsError("审计报告已存在，不覆盖")
    if not destination.parent.is_dir():
        raise ValueError("报告父目录必须存在")
    settings = load_runtime_settings()
    if settings.postgres_host not in ("127.0.0.1", "localhost") or settings.postgres_port != 5433:
        raise ValueError("仅允许专用本地PG5433")
    engine = create_replay_engine(settings)
    engine.sync_engine.echo = False
    engine.sync_engine.hide_parameters = True
    try:
        async with asyncio.timeout(30), engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            await connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            read_only = await connection.scalar(text("SHOW transaction_read_only"))
            if read_only != "on":
                raise RuntimeError("Read-only database transaction required")
            result = await audit_stock_history(connection, as_of=datetime.now(ZoneInfo("Asia/Shanghai")).date())
            result.update(database_access="read_only", transaction_read_only=read_only,
                transaction_isolation=await connection.scalar(text("SHOW transaction_isolation")),
                audited_at=datetime.now(timezone.utc).isoformat(), timezone="Asia/Shanghai")
    finally:
        await engine.dispose()
    with destination.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, allow_nan=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return result


def main():
    parser = argparse.ArgumentParser(description="仅本地PG5433只读审计旧A股历史；0来源/0模型调用，不认证缺失的历史证据")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps({key: result[key] for key in ("status", "summary", "quality", "source_calls", "model_calls")}, ensure_ascii=True))
        return 0
    except Exception as error:
        print(f"Stock history audit failed ({type(error).__name__}); credentials and database content omitted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
