"""forming 分桶交易所时钟 + bar 版本号（一致性机制层）"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.services import kline as kline_svc
from app.services.kline import (
    _exchange_clock,
    _realtime_state_payload,
    update_realtime_klines,
)

_BJ_TZ = timezone(timedelta(hours=8))


class _FakeRedis:
    """最小 Redis 假体：get/set/delete 记录到内存字典"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


def _quote(symbol: str, price: float, volume: int, tick_time: str = "") -> dict:
    return {
        "symbol": symbol,
        "last_price": price,
        "volume": volume,
        "open_price": price,
        "high_price": price,
        "low_price": price,
        "tick_time": tick_time,
    }


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(kline_svc, "_realtime_state", {})
    yield


class TestExchangeClock:
    def test_v2_17digit(self):
        now = datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)
        clock = _exchange_clock({"tick_time": "20260822090030400"}, now)
        assert clock == datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)

    def test_iso_19char(self):
        now = datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)
        clock = _exchange_clock({"tick_time": "2026-08-22 09:00:30"}, now)
        assert clock == datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)

    def test_v1_iso_with_millis(self):
        now = datetime(2026, 8, 22, 21, 15, 2, tzinfo=_BJ_TZ)
        clock = _exchange_clock({"tick_time": "2026-08-22 21:15:02.500"}, now)
        assert clock == datetime(2026, 8, 22, 21, 15, 2, tzinfo=_BJ_TZ)

    def test_time_only_falls_back(self):
        """新浪纯时刻（无日期）不可靠 → 回退本地钟"""
        now = datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)
        assert _exchange_clock({"tick_time": "09:00:30"}, now) is now

    def test_drift_guard(self):
        """交易所钟偏离本地 >5 分钟视为脏数据 → 回退"""
        now = datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)
        clock = _exchange_clock({"tick_time": "20260822091000000"}, now)
        assert clock is now

    def test_missing_tick_time(self):
        now = datetime(2026, 8, 22, 9, 0, 30, tzinfo=_BJ_TZ)
        assert _exchange_clock({}, now) is now


class TestFormingBucketAndVersion:
    @pytest.mark.asyncio
    async def test_bucket_uses_exchange_clock(self, monkeypatch):
        """本地钟 09:00:58，交易所钟 09:01:02 → bar 落 09:01 桶（非本地 09:00）

        夹具用交易日周四 2026-08-20：日历守卫会拒绝非交易日 bar。
        """
        local_now = datetime(2026, 8, 20, 9, 0, 58, tzinfo=_BJ_TZ)
        monkeypatch.setattr(kline_svc, "is_trading_hours", lambda: True)
        monkeypatch.setattr(
            kline_svc, "is_symbol_trading", lambda symbol, now: True
        )
        monkeypatch.setattr(kline_svc, "datetime", _FakeDateTime(local_now))

        redis = _FakeRedis()
        await update_realtime_klines(
            redis,
            [_quote("rb2610", 4000.0, 100, "20260820090102000")],
        )
        state = kline_svc._realtime_state["rb2610"]["1m"]
        assert state["bar_time"] == "2026-08-20 09:01:00"

    @pytest.mark.asyncio
    async def test_version_increments_and_persists(self, monkeypatch):
        local_now = datetime(2026, 8, 20, 9, 0, 10, tzinfo=_BJ_TZ)
        monkeypatch.setattr(kline_svc, "is_trading_hours", lambda: True)
        monkeypatch.setattr(
            kline_svc, "is_symbol_trading", lambda symbol, now: True
        )
        monkeypatch.setattr(kline_svc, "datetime", _FakeDateTime(local_now))

        redis = _FakeRedis()
        updated = await update_realtime_klines(
            redis, [_quote("rb2610", 4000.0, 100)]
        )
        assert updated[0]["bar"]["version"] == 1

        updated = await update_realtime_klines(
            redis, [_quote("rb2610", 4001.0, 120)]
        )
        assert updated[0]["bar"]["version"] == 2
        assert updated[0]["bar"]["close"] == 4001.0

        # Redis 载荷带 version（重启可恢复，不回退）
        import json

        payload = json.loads(redis.store["kline:realtime:rb2610:1m"])
        assert payload["version"] == 2
        assert payload["bar"]["version"] == 2

    def test_state_payload_carries_version(self):
        state = {"bar": {"time": "t"}, "bar_time": "t", "period_start_volume": 0,
                 "open_locked": True, "version": 7}
        assert _realtime_state_payload("rb2610", "1m", state)["version"] == 7

    @pytest.mark.asyncio
    async def test_whole_minute_tick_folds_into_previous_bucket(self, monkeypatch):
        """10:01:00 整分 tick（上一分钟收盘打印）并入 10:00 桶更新 close，
        不开新桶；下一个非整分 tick 才开 10:01 新桶，open 不沾收盘打印价
        （与深历史 pg-tickdata 的 eob 折叠口径同轴）。"""
        local_now = datetime(2026, 8, 20, 10, 0, 30, tzinfo=_BJ_TZ)
        monkeypatch.setattr(kline_svc, "is_trading_hours", lambda: True)
        monkeypatch.setattr(kline_svc, "is_symbol_trading", lambda symbol, now: True)
        monkeypatch.setattr(kline_svc, "datetime", _FakeDateTime(local_now))

        redis = _FakeRedis()
        # 10:00:30 → 开 10:00 桶
        await update_realtime_klines(
            redis, [_quote("rb2610", 4000.0, 100, "20260820100030000")]
        )
        # 10:01:00 整分（10:00 桶的收盘打印）→ 仍 10:00 桶，close/量并入
        updated = await update_realtime_klines(
            redis, [_quote("rb2610", 4001.0, 110, "20260820100100000")]
        )
        bar = updated[0]["bar"]
        assert bar["time"] == "2026-08-20 10:00:00"
        assert bar["close"] == 4001.0
        assert bar["volume"] == 10
        # 10:01:05 → 开 10:01 新桶，open = 该 tick 价（非收盘打印价）
        updated = await update_realtime_klines(
            redis, [_quote("rb2610", 4002.0, 120, "20260820100105000")]
        )
        bar = updated[0]["bar"]
        assert bar["time"] == "2026-08-20 10:01:00"
        assert bar["open"] == 4002.0

    @pytest.mark.asyncio
    async def test_open_whole_minute_tick_starts_new_bucket(self, monkeypatch):
        """09:00:00 开盘整点 tick 归开盘新桶（不折回上一分钟）。"""
        local_now = datetime(2026, 8, 20, 9, 0, 5, tzinfo=_BJ_TZ)
        monkeypatch.setattr(kline_svc, "is_trading_hours", lambda: True)
        monkeypatch.setattr(kline_svc, "is_symbol_trading", lambda symbol, now: True)
        monkeypatch.setattr(kline_svc, "datetime", _FakeDateTime(local_now))

        redis = _FakeRedis()
        await update_realtime_klines(
            redis, [_quote("rb2610", 4000.0, 10, "20260820090000000")]
        )
        state = kline_svc._realtime_state["rb2610"]["1m"]
        assert state["bar_time"] == "2026-08-20 09:00:00"


class _FakeDateTime:
    """datetime 替身：now() 固定，strptime 等类方法透传真 datetime"""

    def __init__(self, fixed):
        self._fixed = fixed
        self._real = datetime

    def now(self, tz=None):
        return self._fixed if tz is not None else self._fixed.replace(tzinfo=None)

    def __getattr__(self, name):
        return getattr(self._real, name)
