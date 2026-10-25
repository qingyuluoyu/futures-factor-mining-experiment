"""TqSdk 全周期 K线修正脚本（手动触发）

用法：
    uv run python scripts/sync-kline-tqsdk.py                    # 全品种全周期
    uv run python scripts/sync-kline-tqsdk.py --code RB           # 单品种（全周期）
    uv run python scripts/sync-kline-tqsdk.py --period 1m         # 全品种单周期
    uv run python scripts/sync-kline-tqsdk.py --code RB --period 1d  # 单品种单周期

从天勤 TqSdk 拉取连续主力 K线，修正 PG kline_minute / kline_history 表。
用于定时任务之外的按需修正（如首次部署、数据回填、验证）。
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# 项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import load_settings  # noqa: E402
from app.data.contracts import ALL_CONTRACTS  # noqa: E402
from app.services.kline_sync import sync_kline_from_tqsdk  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="TqSdk 全周期 K线修正")
    parser.add_argument("--code", type=str, default="", help="只修正指定品种，如 RB")
    parser.add_argument(
        "--period",
        type=str,
        default="",
        choices=["", "1m", "5m", "15m", "30m", "60m", "1d"],
        help="只修正指定周期（默认全部）",
    )
    args = parser.parse_args()

    settings = load_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 构造品种列表
    codes: list[dict[str, str]] = []
    seen: set[str] = set()
    for c in ALL_CONTRACTS:
        code = str(c.get("code") or "").strip().lower()
        exchange = str(c.get("exchange") or "").strip()
        if args.code and code != args.code.lower():
            continue
        if code and exchange and code not in seen:
            seen.add(code)
            codes.append({"code": code, "exchange": exchange})

    if not codes:
        print(f"未找到品种: {args.code}")
        await engine.dispose()
        return

    periods = [args.period] if args.period else None
    print(
        f"开始修正：{len(codes)} 个品种"
        + (f"（{args.code}）" if args.code else "")
        + (f"，周期 {args.period}" if args.period else "，全周期")
    )

    start = time.monotonic()
    stats = await sync_kline_from_tqsdk(
        session_factory,
        redis_conn=None,
        codes=codes,
        periods=periods,
        tqsdk_user=settings.tqsdk_user,
        tqsdk_password=settings.tqsdk_password,
    )
    elapsed = time.monotonic() - start

    print(f"\n完成！耗时 {elapsed:.1f}s")
    for p, n in stats.items():
        print(f"  {p}: {n} 条")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
