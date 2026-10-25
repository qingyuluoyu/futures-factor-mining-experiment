"""A 股数据导入脚本 —— 股票池 / 日线(前复权) / 估值 / 财务（Tushare 协议）

用法：
    uv run python scripts/import-stock-data.py                          # 沪深300 全量
    uv run python scripts/import-stock-data.py --universe csi800        # 中证800
    uv run python scripts/import-stock-data.py --codes 600519,000001    # 指定股票
    uv run python scripts/import-stock-data.py --start-date 20200101    # 指定起始日期
    uv run python scripts/import-stock-data.py --skip-daily             # 只导财务
    uv run python scripts/import-stock-data.py --dry-run                # 只拉不写

逐批拉取 + 落库（2 只/批），中断后重跑自动增量续传。

数据落地：
    stock_instruments  标的静态信息（代码/名称/交易所/行业/上市日期）
    kline_history      前复权日线（code=6位股票代码，hold=0）
    stock_valuation    每日估值（PE(TTM)/PB/总市值/流通市值/换手率）
    stock_financial    季度财务（含公告日期 ann_date → 前视偏差防护）
"""

import argparse
import asyncio
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import load_settings  # noqa: E402
from app.data.stock_history import (  # noqa: E402
    DAILY_BATCH_SIZE,
    FINA_BATCH_SIZE,
    fetch_daily_basic_batch,
    fetch_daily_batch,
    fetch_fina_batch,
    fetch_stock_basic,
    fetch_universe,
)
from app.models.kline_history import KlineHistory  # noqa: E402
from app.models.stock import (  # noqa: E402
    StockFinancial,
    StockInstrument,
    StockValuation,
)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def upsert_instruments(session_factory, universe: list[dict[str, str]]) -> None:
    """写入/更新标的静态信息（行业/上市日期来自 stock_basic）"""
    basics = {b["code"]: b for b in fetch_stock_basic([u["code"] for u in universe])}
    async with session_factory() as session:
        for item in universe:
            basic = basics.get(item["code"], {})
            existing = await session.execute(
                select(StockInstrument).where(StockInstrument.code == item["code"])
            )
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(
                    StockInstrument(
                        code=item["code"],
                        name=item.get("name") or basic.get("name") or item["code"],
                        exchange=item.get("exchange", ""),
                        industry=basic.get("industry", ""),
                        list_date=basic.get("list_date"),
                    )
                )
            else:
                row.name = item.get("name") or basic.get("name") or row.name
                row.industry = basic.get("industry") or row.industry
                row.list_date = basic.get("list_date") or row.list_date
        await session.commit()
    print(f"[标的] {len(universe)} 只已写入/更新", flush=True)


async def _existing_dates(session: AsyncSession, model, code: str) -> set:
    conditions = [model.code == code]
    # kline_history 等双市场表按 market_type 过滤；stock_valuation 为股票专表
    if hasattr(model, "market_type"):
        conditions.append(model.market_type == "stock")
    rows = (
        await session.execute(select(model.trade_date).where(*conditions))
    ).scalars().all()
    return set(rows)


async def _write_kline(session: AsyncSession, code: str, records: list[dict]) -> int:
    for record in records:
        volume = record.get("volume")
        if (isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume)
                or not 0 <= volume <= 2147483647 or not float(volume).is_integer()):
            raise ValueError("旧历史表成交量为整数手，无法无损存储该数据；先迁移字段，禁止截断或填零")
    existing = await _existing_dates(session, KlineHistory, code)
    if existing and any(record["trade_date"] not in existing for record in records):
        raise ValueError("旧前复权历史缺少可核验复权锚点，禁止增量拼接不同基准；需版本化重建，不自动覆盖")
    new = [
        KlineHistory(
            market_type="stock",
            code=code,
            trade_date=r["trade_date"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=int(r["volume"]),
            hold=0,
        )
        for r in records
        if r["trade_date"] not in existing
    ]
    if new:
        session.add_all(new)
        await session.commit()
    return len(new)


async def _codes_with_data(session: AsyncSession, model, latest_date: date) -> set[str]:
    """已有数据且覆盖到 latest_date 的代码集合（跳过重复拉取）"""
    from sqlalchemy import func

    stmt = select(model.code, func.max(model.trade_date))
    # kline_history 等双市场表按 market_type 过滤；stock_valuation 为股票专表
    if hasattr(model, "market_type"):
        stmt = stmt.where(model.market_type == "stock")
    rows = (await session.execute(stmt.group_by(model.code))).all()
    return {code for code, max_d in rows if max_d is not None and max_d >= latest_date}


async def import_daily(session_factory, codes: list[str], start_date: str, dry_run: bool) -> int:
    """逐批导入前复权日线（2 只/批，断点续传）"""
    from datetime import date as _date, timedelta as _td

    async with session_factory() as session:
        covered = await _codes_with_data(session, KlineHistory, _date.today() - _td(days=5))
    pending = [c for c in codes if c not in covered]
    if not pending:
        print(f"[2/4] 前复权日线已全部覆盖（{len(codes)} 只），跳过", flush=True)
        return 0
    batches = _chunks(pending, DAILY_BATCH_SIZE)
    print(f"[2/4] 导入前复权日线（待导入 {len(pending)}/{len(codes)} 只 / {len(batches)} 批）...", flush=True)
    total = 0
    for i, batch in enumerate(batches, 1):
        try:
            data = await asyncio.to_thread(fetch_daily_batch, batch, start_date)
        except Exception as exc:  # noqa: BLE001 — 单批失败跳过，不影响后续
            print(f"    [批 {i}/{len(batches)}] {batch} 拉取失败: {exc}", flush=True)
            continue
        if dry_run:
            total += sum(len(v) for v in data.values())
            continue
        async with session_factory() as session:
            for code, records in data.items():
                total += await _write_kline(session, code, records)
        if i % 10 == 0 or i == len(batches):
            print(f"    批 {i}/{len(batches)}，累计 {total} 条", flush=True)
    print(f"    完成：新增 {total} 条日线", flush=True)
    return total


async def import_valuation(session_factory, codes: list[str], start_date: str, dry_run: bool) -> int:
    """逐批导入每日估值（2 只/批，断点续传）"""
    from datetime import date as _date, timedelta as _td

    async with session_factory() as session:
        covered = await _codes_with_data(session, StockValuation, _date.today() - _td(days=5))
    pending = [c for c in codes if c not in covered]
    if not pending:
        print(f"[3/4] 每日估值已全部覆盖（{len(codes)} 只），跳过", flush=True)
        return 0
    batches = _chunks(pending, DAILY_BATCH_SIZE)
    print(f"[3/4] 导入每日估值（待导入 {len(pending)}/{len(codes)} 只 / {len(batches)} 批）...", flush=True)
    total = 0
    for i, batch in enumerate(batches, 1):
        try:
            data = await asyncio.to_thread(fetch_daily_basic_batch, batch, start_date)
        except Exception as exc:  # noqa: BLE001
            print(f"    [批 {i}/{len(batches)}] {batch} 估值拉取失败: {exc}", flush=True)
            continue
        if dry_run:
            total += sum(len(v) for v in data.values())
            continue
        async with session_factory() as session:
            for code, records in data.items():
                existing = await _existing_dates(session, StockValuation, code)
                new = [
                    StockValuation(
                        code=code,
                        trade_date=r["trade_date"],
                        pe_ttm=r["pe_ttm"],
                        pb=r["pb"],
                        total_mv=r["total_mv"],
                        circ_mv=r["circ_mv"],
                        turnover_rate=r["turnover_rate"],
                    )
                    for r in records
                    if r["trade_date"] not in existing
                ]
                if new:
                    session.add_all(new)
                    await session.commit()
                total += len(new)
        if i % 10 == 0 or i == len(batches):
            print(f"    批 {i}/{len(batches)}，累计 {total} 条", flush=True)
    print(f"    完成：新增 {total} 条估值", flush=True)
    return total


async def import_financials(session_factory, codes: list[str], dry_run: bool) -> int:
    """逐批导入季度财务（10 只/批，含公告日期）"""
    from datetime import date as _date

    # 财务按季更新：最新报告期在 100 天内的代码跳过
    cutoff = _date.today() - timedelta(days=100)
    async with session_factory() as session:
        from sqlalchemy import func

        rows = (
            await session.execute(
                select(StockFinancial.code, func.max(StockFinancial.report_date)).group_by(
                    StockFinancial.code
                )
            )
        ).all()
        covered = {code for code, max_d in rows if max_d is not None and max_d >= cutoff}
    pending = [c for c in codes if c not in covered]
    if not pending:
        print(f"[4/4] 季度财务已最新（{len(codes)} 只），跳过", flush=True)
        return 0
    batches = _chunks(pending, FINA_BATCH_SIZE)
    print(f"[4/4] 导入季度财务（待导入 {len(pending)}/{len(codes)} 只 / {len(batches)} 批）...", flush=True)
    total = 0
    for i, batch in enumerate(batches, 1):
        try:
            data = await asyncio.to_thread(fetch_fina_batch, batch)
        except Exception as exc:  # noqa: BLE001
            print(f"    [批 {i}/{len(batches)}] {batch} 财务拉取失败: {exc}", flush=True)
            continue
        if dry_run:
            total += sum(len(v) for v in data.values())
            continue
        async with session_factory() as session:
            for code, records in data.items():
                written = 0
                for r in records:
                    existing = await session.execute(
                        select(StockFinancial).where(
                            StockFinancial.code == r["code"],
                            StockFinancial.report_date == r["report_date"],
                        )
                    )
                    row = existing.scalar_one_or_none()
                    if row is None:
                        session.add(StockFinancial(**r))
                    else:
                        for k, v in r.items():
                            setattr(row, k, v)
                    written += 1
                await session.commit()
                total += written
        if i % 5 == 0 or i == len(batches):
            print(f"    批 {i}/{len(batches)}，累计 {total} 期", flush=True)
    print(f"    完成：{total} 条财务记录", flush=True)
    return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="A股数据导入（Tushare/DaoShare）")
    parser.add_argument("--universe", choices=["hs300", "csi800"], default="hs300")
    parser.add_argument("--codes", default="", help="逗号分隔的6位代码，覆盖 universe")
    parser.add_argument("--start-date", default="20180101", help="日线/估值起始 YYYYMMDD")
    parser.add_argument("--skip-daily", action="store_true", help="跳过日线+估值")
    parser.add_argument("--skip-financials", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.tushare_token:
        print("[!] .env 未配置 TUSHARE_TOKEN")
        return
    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 幂等建表（后端未重启时也能直接导入）
    from app.models.base import Base  # noqa: E402
    import app.models.kline_history  # noqa: F401,E402
    import app.models.stock  # noqa: F401,E402

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    t0 = time.time()
    try:
        if args.codes:
            universe = [
                {"code": c.strip().zfill(6), "name": "", "exchange": ""}
                for c in args.codes.split(",")
                if c.strip()
            ]
        else:
            print(f"[1/4] 拉取股票池（{args.universe}）...", flush=True)
            universe = await asyncio.to_thread(fetch_universe, args.universe)
            print(f"    共 {len(universe)} 只", flush=True)

        if not universe:
            print("[!] 股票池为空，终止")
            return

        if not args.dry_run:
            await upsert_instruments(session_factory, universe)

        codes = [u["code"] for u in universe]
        if not args.skip_daily:
            await import_daily(session_factory, codes, args.start_date, args.dry_run)
            await import_valuation(session_factory, codes, args.start_date, args.dry_run)
        if not args.skip_financials:
            await import_financials(session_factory, codes, args.dry_run)
    except Exception as exc:  # noqa: BLE001 — 后台运行时也要留下痕迹
        print(f"[!] 导入中断: {type(exc).__name__}: {exc}", flush=True)
        raise
    finally:
        await engine.dispose()
    print(f"全部完成，耗时 {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
