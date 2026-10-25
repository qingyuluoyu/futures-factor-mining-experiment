"""启动对账修正：主 PG 权威 vs Redis 缓存（桌面端启动触发）"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import kline, kline_repair

_BJ = timezone(timedelta(hours=8))


class _FakeRedis:
    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(store or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


def _bar(time: str, close: float = 3000.0, volume: int = 100) -> dict[str, Any]:
    return {
        "time": time,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
    }


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSessionFactory:
    def __call__(self):
        return _FakeSession()


def _now_str(days_ago: float = 0) -> str:
    t = datetime.now(_BJ).replace(tzinfo=None) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


async def _fake_masters(redis_conn) -> list[dict[str, str]]:
    return [
        {"product_code": "PP", "symbol": "pp2701", "name": "pp",
         "product_name": "pp", "contract_name": "pp2701"},
    ]


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):
    """受控环境：固定主力宇宙 + 可编程 PG 数据 + 日历过滤恒等。

    日历过滤恒等是为了套件时间无关（夹具时间相对真实 now 构造，
    非交易时段运行会被误滤）；过滤本身有自己的测试覆盖。
    """
    monkeypatch.setattr(kline_repair, "list_all_master_symbols", _fake_masters)
    monkeypatch.setattr(
        kline_repair, "_filter_non_trading_bars", lambda bars, period: bars
    )
    pg_data: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def _fake_pg(session, symbol, period):
        return list(pg_data.get((symbol, period), []))

    monkeypatch.setattr(kline_repair, "_fetch_from_pg", _fake_pg)
    return pg_data


@pytest.mark.asyncio
async def test_repair_replaces_bad_bar_and_fills_missing(setup):
    pg = setup
    # PG 权威：窗口内两根（一根缓存里有但坏，一根缓存缺）
    pg[("pp2701", "5m")] = [
        _bar(_now_str(1.0), close=3010.0, volume=200),   # 缓存里是坏的
        _bar(_now_str(0.9), close=3015.0, volume=150),   # 缓存缺失
    ]
    cached = [
        _bar(_now_str(2.5), close=3000.0),               # 窗口外不动
        _bar(_now_str(1.0), close=8142.0, volume=0),     # 坏 bar（残缺）
    ]
    key = kline._history_cache_key("pp2701", "5m")
    r = _FakeRedis({key: json.dumps(cached)})

    out = await kline_repair.verify_repair_kline_cache(_FakeSession(), r)

    bars = json.loads(r.store[key])
    by_time = {b["time"]: b for b in bars}
    fixed = by_time[_now_str(1.0)]
    assert (fixed["close"], fixed["volume"]) == (3010.0, 200)  # PG 覆盖
    assert _now_str(0.9) in by_time                           # 缺根补齐
    assert by_time[_now_str(2.5)]["close"] == 3000.0          # 窗口外未动
    assert out["repaired_bars"] == 2
    assert out["repaired_keys"] >= 1


@pytest.mark.asyncio
async def test_no_diff_no_write(setup):
    pg = setup
    good = [_bar(_now_str(1.0), close=3010.0, volume=200)]
    pg[("pp2701", "5m")] = list(good)
    key = kline._history_cache_key("pp2701", "5m")
    r = _FakeRedis({key: json.dumps(good)})

    out = await kline_repair.verify_repair_kline_cache(_FakeSession(), r)

    assert out["repaired_bars"] == 0
    # 未修正时缓存原样（内容不变）
    assert json.loads(r.store[key]) == good


@pytest.mark.asyncio
async def test_no_cache_no_preseed(setup):
    setup[("pp2701", "5m")] = [_bar(_now_str(1.0), close=3010.0)]
    r = _FakeRedis()

    out = await kline_repair.verify_repair_kline_cache(_FakeSession(), r)

    assert kline._history_cache_key("pp2701", "5m") not in r.store
    assert out["repaired_bars"] == 0


@pytest.mark.asyncio
async def test_out_of_window_pg_ignored(setup):
    """窗口外的 PG 数据不参与对账（旧数据不回写）。"""
    setup[("pp2701", "5m")] = [
        _bar(_now_str(10.0), close=9999.0),  # 10 天前，窗口外
    ]
    cached = [_bar(_now_str(0.5), close=3010.0)]
    key = kline._history_cache_key("pp2701", "5m")
    r = _FakeRedis({key: json.dumps(cached)})

    out = await kline_repair.verify_repair_kline_cache(_FakeSession(), r)

    assert out["repaired_bars"] == 0
    bars = json.loads(r.store[key])
    assert len(bars) == 1 and bars[0]["close"] == 3010.0


@pytest.mark.asyncio
async def test_weekend_phantom_pg_bars_filtered(monkeypatch: pytest.MonkeyPatch):
    """PG 里的周末幽灵 bar（主库固化过）不得回流缓存。"""
    # 固定时钟到周一午间：夹具周五/周日恒在对账窗口内（VERIFY_DAYS=3）。
    # 修复日期腐烂——硬编码 8-21 自 8-25 起越窗，master 上已偶发红。
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 24, 12, 0, tzinfo=tz)

    monkeypatch.setattr(kline_repair, "datetime", _Clock)

    # 真实日历过滤：周日 2026-08-23 的 bar 应被剔除
    monkeypatch.setattr(kline_repair, "list_all_master_symbols", _fake_masters)

    async def _fake_pg(session, symbol, period):
        return [
            _bar("2026-08-21 22:00:00", close=8142.0, volume=500),  # 周五夜盘 ✓
            _bar("2026-08-23 22:00:00", close=8142.0, volume=0),    # 周日幽灵 ✗
        ]

    monkeypatch.setattr(kline_repair, "_fetch_from_pg", _fake_pg)
    cached = [
        _bar("2026-08-21 22:00:00", close=8100.0),   # 与 PG 差异 → 修正
        _bar("2026-08-23 22:00:00", close=8142.0),   # 已在缓存里的幽灵 → 保留现状
    ]
    key = kline._history_cache_key("pp2701", "5m")
    r = _FakeRedis({key: json.dumps(cached)})

    await kline_repair.verify_repair_kline_cache(_FakeSession(), r)

    bars = json.loads(r.store[key])
    by_time = {b["time"]: b for b in bars}
    assert by_time["2026-08-21 22:00:00"]["close"] == 8142.0  # 修正为 PG 值
    # 幽灵键未被"修正"（PG 侧已滤掉，不参与）——缓存内容保持
    assert len(bars) == 2
