"""AKShare 日线历史数据导入脚本

用法：
    uv run python scripts/import-kline-history.py                    # 全量导入
    uv run python scripts/import-kline-history.py --code AU          # 单品种导入
    uv run python scripts/import-kline-history.py --start-date 20200101  # 指定起始日期
    uv run python scripts/import-kline-history.py --dry-run          # 只拉不写
"""

import argparse
import asyncio
import sys
import time
from datetime import date as date_type, datetime, timedelta
from pathlib import Path

# 项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession  # noqa: E402
from sqlalchemy import select, func  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import load_settings  # noqa: E402
from app.data.contracts import ALL_CONTRACTS  # noqa: E402
from app.data.akshare_history import fetch_daily_history  # noqa: E402
from app.models.kline_history import KlineHistory  # noqa: E402
from app.models.base import Base  # noqa: E402


def _unique_codes() -> list[dict[str, str]]:
    """从 ALL_CONTRACTS 中提取去重的品种代码"""
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for c in ALL_CONTRACTS:
        code = c["code"]
        if code not in seen:
            seen.add(code)
            result.append({"code": code, "name": c["name"]})
    return result


async def get_latest_date(
    session: AsyncSession,
    code: str,
) -> str | None:
    """查询品种已有的最新日期"""
    stmt = select(func.max(KlineHistory.trade_date)).where(
        KlineHistory.code == code,
    )
    result = await session.execute(stmt)
    latest = result.scalar_one_or_none()
    return latest.isoformat() if latest else None


async def import_symbol(
    session_factory: sessionmaker,
    code: str,
    start_date: str,
    dry_run: bool,
) -> int:
    """导入单个品种的日线历史数据，返回新增记录数"""
    records = await asyncio.to_thread(fetch_daily_history, code, start_date)
    if not records:
        print(f"  [{code}] 无数据返回（start_date={start_date}）")
        return 0

    if dry_run:
        first = records[0]["trade_date"]
        last = records[-1]["trade_date"]
        print(f"  [{code}] dry-run: {len(records)} 条，范围 {first} ~ {last}")
        return len(records)

    async with session_factory() as session:
        # 去重：查出已有日期集合
        existing = await _get_existing_dates(session, code)
        new_records = [r for r in records if r["trade_date"] not in existing]
        if not new_records:
            print(f"  [{code}] 全部已存在，跳过")
            return 0

        objs = [
            KlineHistory(
                code=code,
                trade_date=date_type.fromisoformat(r["trade_date"]),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
                hold=r["hold"],
            )
            for r in new_records
        ]
        session.add_all(objs)
        await session.commit()

        first = new_records[0]["trade_date"]
        last = new_records[-1]["trade_date"]
        print(f"  [{code}] 插入 {len(new_records)} 条，范围 {first} ~ {last}")
        return len(new_records)


async def _get_existing_dates(
    session: AsyncSession,
    code: str,
) -> set[str]:
    """查询品种已有的所有日期"""
    stmt = select(KlineHistory.trade_date).where(KlineHistory.code == code)
    result = await session.execute(stmt)
    return {row[0].isoformat() for row in result.all()}


async def main() -> None:
    """遍历品种列表，逐品种导入日线历史"""
    parser = argparse.ArgumentParser(description="导入 AKShare 日线历史数据")
    parser.add_argument("--code", type=str, default="", help="只导入指定品种，如 AU")
    parser.add_argument("--start-date", type=str, default="20000101", help="起始日期 YYYYMMDD")
    parser.add_argument("--dry-run", action="store_true", help="只拉取不写入")
    args = parser.parse_args()

    db_url = load_settings().database_url
    engine = create_async_engine(db_url, echo=False)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    codes = _unique_codes()
    if args.code:
        codes = [c for c in codes if c["code"] == args.code.upper()]
        if not codes:
            print(f"未找到品种: {args.code}")
            return

    total = 0
    start_time = time.time()

    for item in codes:
        code = item["code"]
        print(f"[{code}] {item['name']} 开始导入...")

        # 查询已有最新日期，增量导入
        if not args.dry_run:
            async with session_factory() as s:
                latest = await get_latest_date(s, code)
            if latest:
                next_day = (datetime.fromisoformat(latest) + timedelta(days=1)).strftime("%Y%m%d")
            else:
                next_day = args.start_date
        else:
            next_day = args.start_date

        try:
            count = await import_symbol(session_factory, code, next_day, args.dry_run)
            total += count
        except Exception as e:
            print(f"  [{code}] 导入失败: {e}")

    elapsed = time.time() - start_time
    print(f"\n完成！共导入 {total} 条，耗时 {elapsed:.1f}s")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
