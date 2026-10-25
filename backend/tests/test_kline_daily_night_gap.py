"""夜盘日线当日闭合缺口 + tickdata 同步断档修复测试

背景：2026-08-24 晚 21:00 夜盘开盘后图上 8-24 日线消失。三因叠加：
1. 新浪日线当晚才更新 8-24 闭合 bar，日线 4 天宽限放行尾部 8-21 的序列
   并覆盖缓存（缺当日闭合无守卫）；
2. 58 当天两次部署重建 Redis → 主力缓存清空 → tickdata 同步"0 合约"空转
   （_sync_symbols 空缓存不回退静态表）；
3. 同步循环休市期启动把下一轮排 +6h，21:00 夜盘开盘不触发 → 当晚
   pg-tickdata 一直缺 8-24（兜底链失效）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from app.services import kline, vvtr_tickdata_sync as sync

_BJ = timezone(timedelta(hours=8))


def _bj(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=_BJ)


# ========== 1. 夜盘日线当日闭合下限 ==========

def test_daily_closed_floor_night_windows():
    # 夜盘 21:00 后：当日白盘已收 → 下限为今天
    assert sync_floor(kline._daily_closed_floor(_bj(2026, 8, 24, 21, 30))) == "2026-08-24"
    # 跨午夜夜盘（次日 00:00-02:30）：应含昨日自然日
    assert sync_floor(kline._daily_closed_floor(_bj(2026, 8, 25, 0, 30))) == "2026-08-24"
    assert sync_floor(kline._daily_closed_floor(_bj(2026, 8, 25, 2, 29))) == "2026-08-24"
    # 白盘时段 / 日间休市：不设下限
    assert kline._daily_closed_floor(_bj(2026, 8, 24, 10, 0)) is None
    assert kline._daily_closed_floor(_bj(2026, 8, 24, 15, 30)) is None
    assert kline._daily_closed_floor(_bj(2026, 8, 24, 3, 30)) is None


def sync_floor(d):
    return d.strftime("%Y-%m-%d") if d else None


def test_daily_floor_missing_night():
    bars_821 = [{"time": "2026-08-21", "close": 1.0}]
    bars_824 = [{"time": "2026-08-24", "close": 1.0}]
    # 夜盘 21:30（8-24 晚）：尾部 8-21 → 缺当日；尾部 8-24 → 通过
    assert kline._daily_floor_missing(bars_821, _bj(2026, 8, 24, 21, 30)) is True
    assert kline._daily_floor_missing(bars_824, _bj(2026, 8, 24, 21, 30)) is False
    # 跨午夜（8-25 凌晨）：下限 8-24
    assert kline._daily_floor_missing(bars_824, _bj(2026, 8, 25, 0, 30)) is False
    assert kline._daily_floor_missing(bars_821, _bj(2026, 8, 25, 0, 30)) is True
    # 白盘时段不收紧
    assert kline._daily_floor_missing(bars_821, _bj(2026, 8, 24, 10, 0)) is False


def test_stale_gate_rejects_daily_floor_missing(monkeypatch):
    """gate 接线：夜盘缺当日闭合 → stale gate 返回 True（拒）"""
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda s, now=None: True)
    # 相对日期构造：硬编码日期会随时间流逝超过日线 4 天宽限而误报
    bj = timezone(timedelta(hours=8))
    yesterday = (datetime.now(bj) - timedelta(days=1)).strftime("%Y-%m-%d")
    ten_days_ago = (datetime.now(bj) - timedelta(days=10)).strftime("%Y-%m-%d")
    monkeypatch.setattr(
        kline,
        "_daily_floor_missing",
        lambda bars, now_bj=None: bars[-1]["time"] < yesterday,
    )
    gate = kline._build_latest_stale_gate("1d", "rb2610")
    assert gate is not None
    assert gate([{"time": ten_days_ago}]) is True
    assert gate([{"time": yesterday}]) is False


# ========== 2. tickdata 同步：空主力缓存回退静态表 ==========

def test_sync_symbols_empty_cache_falls_back(monkeypatch):
    from app.services import contract_master

    async def empty_masters(_redis):
        return {}

    monkeypatch.setattr(contract_master, "get_master_contracts", empty_masters)
    symbols = asyncio.run(sync._sync_symbols(None))
    from app.data.contracts import ALL_CONTRACTS

    assert symbols == [c["symbol"].lower() for c in ALL_CONTRACTS]
    assert symbols  # 静态表非空，绝不空转


def test_sync_symbols_normal_cache(monkeypatch):
    from app.services import contract_master

    async def masters(_redis):
        return {"RB": {"master": "rb2610"}, "MA": {"master": "MA2701"}}

    monkeypatch.setattr(contract_master, "get_master_contracts", masters)
    symbols = asyncio.run(sync._sync_symbols(None))
    assert symbols == ["rb2610", "ma2701"]


# ========== 3. 同步循环：夜盘开盘提前触发（纯函数化判定） ==========

def test_regular_due_early_on_trading():
    """盘中提前触发：休市排下的 6h 未到，但已进入交易时段且距上轮 ≥1h"""
    mono = 10_000.0
    assert (
        sync._regular_due(
            now_mono=mono,
            next_regular=mono + 5 * 3600,  # 休市排的 6h 轮
            last_run_mono=mono - 3601,     # 上轮在 1 小时前
            trading=True,
        )
        is True
    )
    # 交易时段但距上轮不足 1h：不触发
    assert (
        sync._regular_due(
            now_mono=mono,
            next_regular=mono + 5 * 3600,
            last_run_mono=mono - 1800,
            trading=True,
        )
        is False
    )
    # 休市且常规轮未到：不触发
    assert (
        sync._regular_due(
            now_mono=mono,
            next_regular=mono + 5 * 3600,
            last_run_mono=mono - 3601,
            trading=False,
        )
        is False
    )
    # 常规轮到期：无条件触发
    assert (
        sync._regular_due(
            now_mono=mono,
            next_regular=mono,
            last_run_mono=mono - 60,
            trading=False,
        )
        is True
    )
