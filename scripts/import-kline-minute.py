"""TqSdk 历史分钟线导入脚本 —— 从天勤拉取 1m K线写入 PG kline_minute

用法：
    uv run python scripts/import-kline-minute.py                    # 全量导入（所有品种 1m）
    uv run python scripts/import-kline-minute.py --code rb          # 单品种
    uv run python scripts/import-kline-minute.py --code rb --period 15m  # 指定周期
    uv run python scripts/import-kline-minute.py --dry-run          # 只拉不写

免费版每品种每次最多 8964 根（1m 约 2-3 周，15m 约 6 个月）。
凭据从 .env 的 TQSDK_USER/TQSDK_PASSWORD 读取（快期账户）。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import load_settings  # noqa: E402
from app.data.contracts import ALL_CONTRACTS  # noqa: E402
from app.data.tqsdk_history import fetch_minute_history  # noqa: E402
from app.services.kline_minute import upsert_minute_bars  # noqa: E402


def _unique_codes() -> list[dict[str, str]]:
    """从 ALL_CONTRACTS 提取去重的品种（code + exchange + name）"""
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for c in ALL_CONTRACTS:
        code = c["code"]
        if code not in seen:
            seen.add(code)
            result.append({"code": code, "exchange": c["exchange"], "name": c["name"]})
    return result


async def import_symbol(
    session_factory: sessionmaker,
    code: str,
    exchange_cn: str,
    period: str,
    user: str,
    password: str,
    dry_run: bool,
) -> int:
    """导入单个品种的分钟线，返回新增记录数"""
    records = await asyncio.to_thread(
        fetch_minute_history, code, exchange_cn, period, 8964, user, password
    )
    if not records:
        print(f"  [{code}] 无数据返回（{exchange_cn} {period}）")
        return 0

    if dry_run:
        first = records[0]["time"]
        last = records[-1]["time"]
        print(f"  [{code}] dry-run: {len(records)} 条，范围 {first} ~ {last}")
        return len(records)

    async with session_factory() as session:
        symbol = records[0].get("symbol", code.lower())
        count = await upsert_minute_bars(session, code.lower(), symbol, period, records)
        first = records[0]["time"]
        last = records[-1]["time"]
        print(f"  [{code}] 写入 {count} 条 {period}（{first} ~ {last}）")
        return count


async def main() -> None:
    parser = argparse.ArgumentParser(description="导入 TqSdk 历史分钟线数据")
    parser.add_argument("--code", type=str, default="", help="只导入指定品种，如 rb")
    parser.add_argument(
        "--period", type=str, default="1m", help="周期 1m/5m/15m/30m/60m，默认 1m"
    )
    parser.add_argument("--dry-run", action="store_true", help="只拉取不写入")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.tqsdk_user or not settings.tqsdk_password:
        print("⚠️  未配置 TQSDK_USER/TQSDK_PASSWORD，请在 .env 设置快期账户凭据")
        return

    db_url = settings.database_url
    engine = create_async_engine(db_url, echo=False)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    codes = _unique_codes()
    if args.code:
        codes = [c for c in codes if c["code"].lower() == args.code.lower()]
        if not codes:
            print(f"未找到品种: {args.code}")
            return

    total = 0
    start_time = time.time()

    for item in codes:
        code = item["code"]
        print(f"[{code}] {item['name']}（{item['exchange']}）开始导入 {args.period}...")
        try:
            count = await import_symbol(
                session_factory,
                code.lower(),
                item["exchange"],
                args.period,
                settings.tqsdk_user,
                settings.tqsdk_password,
                args.dry_run,
            )
            total += count
        except Exception as e:
            print(f"  [{code}] 导入失败: {e}")

    elapsed = time.time() - start_time
    print(f"\n完成！共导入 {total} 条 {args.period}，耗时 {elapsed:.1f}s")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
