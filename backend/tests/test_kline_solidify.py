"""跨周期已收盘 bar 固化测试

防止 live 分钟 K 线跨周期时旧 forming bar 丢失（新浪偶发永久丢点），
导致 AI 任务 extract_closed_bar 取到更旧的 bar 而 no_new_bar skip、静默停摆。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import kline


class _FakeRedis:
    """记录 get/set 的假 Redis（异步接口）"""

    def __init__(self, store: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(store or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _bar(time: str, close: float = 3000.0) -> dict[str, Any]:
    return {
        "time": time,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
    }


@pytest.mark.asyncio
async def test_append_closed_bar_appends_when_cache_exists() -> None:
    """历史缓存存在时，把新 bar 追加到末尾（不丢旧 bar）"""
    existing = [_bar("2026-07-29 10:00:00"), _bar("2026-07-29 10:15:00")]
    r = _FakeRedis(
        {kline._history_cache_key("ta2609", "15m"): json.dumps(existing)}
    )

    await kline.append_closed_bar(r, "ta2609", "15m", _bar("2026-07-29 10:30:00"))

    raw = r.store[kline._history_cache_key("ta2609", "15m")]
    bars = json.loads(raw)
    times = [b["time"] for b in bars]
    assert times == ["2026-07-29 10:00:00", "2026-07-29 10:15:00", "2026-07-29 10:30:00"]


@pytest.mark.asyncio
async def test_append_closed_bar_replaces_same_time() -> None:
    """同键 bar 被覆盖（幂等，不重复）"""
    existing = [_bar("2026-07-29 10:15:00", close=2900.0)]
    r = _FakeRedis(
        {kline._history_cache_key("ta2609", "15m"): json.dumps(existing)}
    )

    # 同时间 10:15 但 close 不同 → 应覆盖而非追加
    await kline.append_closed_bar(r, "ta2609", "15m", _bar("2026-07-29 10:15:00", close=3100.0))

    bars = json.loads(r.store[kline._history_cache_key("ta2609", "15m")])
    assert len(bars) == 1
    assert bars[0]["close"] == 3100.0


@pytest.mark.asyncio
async def test_append_closed_bar_skips_when_cache_absent() -> None:
    """历史缓存不存在时不报错、不创建空 list（交由 get_kline_history 从新浪重拉）"""
    r = _FakeRedis()  # 空 store

    await kline.append_closed_bar(r, "ta2609", "15m", _bar("2026-07-29 10:30:00"))

    # 不应新建历史 key（避免覆盖新浪真实历史）
    assert kline._history_cache_key("ta2609", "15m") not in r.store


@pytest.mark.asyncio
async def test_append_closed_bar_ignores_empty_bar() -> None:
    """空 bar / 无 time 直接返回，不动缓存"""
    existing = [_bar("2026-07-29 10:15:00")]
    r = _FakeRedis(
        {kline._history_cache_key("ta2609", "15m"): json.dumps(existing)}
    )

    await kline.append_closed_bar(r, "ta2609", "15m", {})
    await kline.append_closed_bar(r, "ta2609", "15m", {"open": 1})  # 无 time

    bars = json.loads(r.store[kline._history_cache_key("ta2609", "15m")])
    assert len(bars) == 1  # 未被改动


@pytest.mark.asyncio
async def test_purge_solidifies_closed_bar_before_dropping(monkeypatch) -> None:
    """休市期间迟到/重放 tick 触发清理分支时，已收盘末根先固化再清态。

    回归（2026-08-25）：夜盘收 23:00 后，周末重放/隔夜迟到帧进入清理
    分支直接丢状态——夜盘末根 22:45 只能依赖外部源回补，源滞后时
    刷新即丢根。修复后清理前先 append 进历史缓存。
    """
    kline._realtime_state.clear()
    kline._realtime_state["pp2701"] = {
        "15m": {
            "bar": _bar("2026-08-24 22:45:00", close=8147.0),
            "bar_time": "2026-08-24 22:45:00",
            "period_start_volume": 100000,
            "open_locked": True,
        }
    }
    hist_key = kline._history_cache_key("pp2701", "15m")
    r = _FakeRedis({hist_key: json.dumps([_bar("2026-08-24 22:30:00")])})

    # 粗粒度市场开（夜盘时段），品种级休市（23:00 已收）→ 走清理分支
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now: False)
    rolled = datetime(2026, 8, 25, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(
        kline,
        "_get_period_start_time",
        lambda period, now, symbol="": rolled,
    )
    monkeypatch.setattr(kline, "_is_minute_bar_in_session", lambda s: True)

    await kline.update_realtime_klines(
        r, [{"symbol": "pp2701", "last_price": 8113.0, "volume": 100500}]
    )

    bars = json.loads(r.store[hist_key])
    times = [b["time"] for b in bars]
    assert "2026-08-24 22:45:00" in times
    solidified = next(b for b in bars if b["time"] == "2026-08-24 22:45:00")
    assert solidified["close"] == 8147.0
    # 状态与 Redis forming 键都已清掉
    assert kline._realtime_state.get("pp2701", {}).get("15m") is None
    assert kline._realtime_cache_key("pp2701", "15m") not in r.store
    kline._realtime_state.clear()


@pytest.mark.asyncio
async def test_update_realtime_klines_solidifies_closed_bar_on_period_rollover(
    monkeypatch,
) -> None:
    """update_realtime_klines 跨周期时把旧 bar 固化进历史缓存

    核心回归点：修复前跨周期直接丢弃旧 bar，新浪若丢点则该 bar 永久缺失。
    """
    # 预置：历史缓存已有 10:00、10:15；内存 state 停在 10:15（forming）
    kline._realtime_state.clear()
    kline._realtime_state["ta2609"] = {
        "15m": {
            "bar": _bar("2026-07-29 10:15:00", close=2950.0),
            "bar_time": "2026-07-29 10:15:00",
            "period_start_volume": 1000,
            "open_locked": True,
        }
    }
    hist_key = kline._history_cache_key("ta2609", "15m")
    r = _FakeRedis(
        {hist_key: json.dumps([_bar("2026-07-29 10:00:00"), _bar("2026-07-29 10:15:00")])}
    )

    # 让时段判断放行（交易中 + 品种可交易）
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now: True)
    # 让 _get_period_start_time 返回 10:30（跨入新周期，与 state 的 10:15 不同）
    bj = timezone(timedelta(hours=8))
    rolled = datetime(2026, 7, 29, 10, 31, 0, tzinfo=bj)
    monkeypatch.setattr(
        kline,
        "_get_period_start_time",
        lambda period, now, symbol="": rolled.replace(minute=30, second=0),
    )
    monkeypatch.setattr(kline, "_is_minute_bar_in_session", lambda s: True)

    quotes = [{"symbol": "ta2609", "last_price": 3000.0, "volume": 2000, "position": 100}]

    await kline.update_realtime_klines(r, quotes)

    # 旧 bar（10:15 close=2950）应被固化进历史缓存
    bars = json.loads(r.store[hist_key])
    times = [b["time"] for b in bars]
    assert "2026-07-29 10:15:00" in times
    solidified = next(b for b in bars if b["time"] == "2026-07-29 10:15:00")
    assert solidified["close"] == 2950.0

    kline._realtime_state.clear()


@pytest.mark.asyncio
async def test_update_realtime_preserves_30m_and_60m_across_morning_break(
    monkeypatch,
) -> None:
    """10:15 小休不能清空仍跨休盘累计的 30m/60m forming 桶（起点语义：桶 10:00 起）。"""
    bj = timezone(timedelta(hours=8))

    class _Clock(datetime):
        current = datetime(2026, 7, 29, 10, 14, tzinfo=bj)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(kline, "datetime", _Clock)
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(
        kline,
        "is_symbol_trading",
        lambda symbol, now: not (now.hour == 10 and 15 < now.minute < 30),
    )
    kline._realtime_state.clear()
    r = _FakeRedis()

    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 100.0, "volume": 1000}]
    )
    _Clock.current = datetime(2026, 7, 29, 10, 20, tzinfo=bj)
    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 999.0, "volume": 1100}]
    )
    _Clock.current = datetime(2026, 7, 29, 10, 31, tzinfo=bj)
    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 110.0, "volume": 1200}]
    )

    for period, end in (("30m", "10:00:00"), ("60m", "10:00:00")):
        state = kline._realtime_state["rb2610"][period]
        assert state["bar_time"] == f"2026-07-29 {end}"
        assert state["bar"]["open"] == 100.0
        assert state["bar"]["high"] == 110.0
        assert state["bar"]["volume"] == 200

    kline._realtime_state.clear()


@pytest.mark.asyncio
async def test_update_realtime_preserves_long_buckets_across_lunch(monkeypatch) -> None:
    """11:30 午休不能重置跨到下午的 30m/60m OHLCV。"""
    bj = timezone(timedelta(hours=8))

    class _Clock(datetime):
        current = datetime(2026, 7, 29, 11, 29, tzinfo=bj)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(kline, "datetime", _Clock)
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(
        kline,
        "is_symbol_trading",
        lambda symbol, now: not (
            11 < now.hour < 13 or (now.hour == 11 and now.minute > 30)
        ),
    )
    kline._realtime_state.clear()
    r = _FakeRedis()

    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 100.0, "volume": 2000}]
    )
    _Clock.current = datetime(2026, 7, 29, 12, 0, tzinfo=bj)
    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 999.0, "volume": 2100}]
    )
    _Clock.current = datetime(2026, 7, 29, 13, 31, tzinfo=bj)
    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 90.0, "volume": 2300}]
    )

    for period, end in (("30m", "11:15:00"), ("60m", "11:15:00")):
        state = kline._realtime_state["rb2610"][period]
        assert state["bar_time"] == f"2026-07-29 {end}"
        assert state["bar"]["open"] == 100.0
        assert state["bar"]["low"] == 90.0
        assert state["bar"]["volume"] == 300

    kline._realtime_state.clear()


@pytest.mark.asyncio
async def test_update_realtime_restores_au_60m_across_overnight_gap(monkeypatch) -> None:
    """进程状态丢失后，Redis 仍应恢复 AU 跨 02:30 到 09:30 的 60m 桶。"""
    bj = timezone(timedelta(hours=8))

    class _Clock(datetime):
        current = datetime(2026, 7, 29, 2, 15, tzinfo=bj)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(kline, "datetime", _Clock)
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(
        kline,
        "is_symbol_trading",
        lambda symbol, now: now.hour == 2 or now.hour == 9,
    )
    kline._realtime_state.clear()
    r = _FakeRedis()

    await kline.update_realtime_klines(
        r, [{"symbol": "au2608", "last_price": 100.0, "volume": 1000}]
    )
    kline._realtime_state.clear()  # 模拟进程重启，只保留 Redis
    _Clock.current = datetime(2026, 7, 29, 3, 0, tzinfo=bj)
    await kline.update_realtime_klines(
        r, [{"symbol": "au2608", "last_price": 999.0, "volume": 1100}]
    )
    _Clock.current = datetime(2026, 7, 29, 9, 1, tzinfo=bj)
    await kline.update_realtime_klines(
        r, [{"symbol": "au2608", "last_price": 110.0, "volume": 1200}]
    )

    state = kline._realtime_state["au2608"]["60m"]
    assert state["bar_time"] == "2026-07-29 02:00:00"
    assert state["bar"]["open"] == 100.0
    assert state["bar"]["high"] == 110.0
    assert state["bar"]["volume"] == 200

    kline._realtime_state.clear()


@pytest.mark.asyncio
async def test_recovered_state_rollover_not_solidified(monkeypatch) -> None:
    """重启恢复的残缺 forming 收线不固化（防覆盖权威收盘值）

    回归：2026-08-21 发布重启后，恢复态 21:15 bar 只含重启后少量快照，
    收线固化把残缺 OHLC/Vol 写进历史缓存，覆盖了 vvtr 的正确收盘值。
    恢复态必须跳过固化，交权威历史源供数。
    """
    r = _FakeRedis()
    rt_key = kline._realtime_cache_key("ma2701", "15m")
    r.store[rt_key] = json.dumps(
        {
            "bar": _bar("2026-08-21 21:15:00", close=2727.0),
            "bar_time": "2026-08-21 21:15:00",
            "period_start_volume": 36000,
            "open_locked": True,
        }
    )
    hist_key = kline._history_cache_key("ma2701", "15m")
    r.store[hist_key] = json.dumps([_bar("2026-08-21 21:00:00")])

    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now: True)
    monkeypatch.setattr(kline, "_is_minute_bar_in_session", lambda s: True)
    rolled = datetime(2026, 8, 21, 21, 31)
    monkeypatch.setattr(
        kline,
        "_get_period_start_time",
        lambda period, now, symbol="": rolled.replace(minute=30, second=0),
    )

    appended: list[str] = []

    async def _fake_append(redis, symbol, period, bar):
        appended.append(str(bar.get("time")))

    monkeypatch.setattr(kline, "append_closed_bar", _fake_append)

    # 场景 1：Redis 恢复态（recovered=True）跨周期 → 不固化
    kline._realtime_state.clear()
    await kline.update_realtime_klines(
        r,
        [{"symbol": "ma2701", "last_price": 2731.0, "volume": 36152, "position": 1}],
    )
    assert appended == []
    kline._realtime_state.clear()

    # 场景 2（对照）：进程内正常态（本周期完整聚合）跨周期 → 照常固化
    kline._realtime_state["ma2701"] = {
        "15m": {
            "bar": _bar("2026-08-21 21:15:00", close=2727.0),
            "bar_time": "2026-08-21 21:15:00",
            "period_start_volume": 36000,
            "open_locked": True,
        }
    }
    await kline.update_realtime_klines(
        r,
        [{"symbol": "ma2701", "last_price": 2731.0, "volume": 36152, "position": 1}],
    )
    assert appended == ["2026-08-21 21:15:00"]
    kline._realtime_state.clear()


async def test_persist_1m_only_writes_master_contract(monkeypatch) -> None:
    """1m PG 固化只写当前主力(回归:移仓期新旧合约交替写品种键 → 分时坏 K)

    2026-08-24 fb 实测:fb2609(旧主力)与 fb2611(新主力)的 1m bar 交替
    写进 code='fb' 品种序列,分时图 ±7% 横跳。修复后非主力合约的 1m
    不再写 PG。
    """
    from unittest.mock import AsyncMock

    kline._realtime_state.clear()
    bj = timezone(timedelta(hours=8))
    for sym in ("fb2611", "fb2609"):
        kline._realtime_state[sym] = {
            "1m": {
                "bar": _bar("2026-08-20 10:15:00", close=1268.0 if sym == "fb2611" else 1171.5),
                "bar_time": "2026-08-20 10:15:00",
                "period_start_volume": 100,
                "open_locked": True,
            }
        }
    r = _FakeRedis()

    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now: True)
    rolled = datetime(2026, 8, 20, 10, 16, 0, tzinfo=bj)
    monkeypatch.setattr(
        kline,
        "_get_period_start_time",
        lambda period, now, symbol="": rolled.replace(second=0),
    )
    monkeypatch.setattr(kline, "_is_minute_bar_in_session", lambda s: True)

    async def _masters(redis_conn):
        return {"fb": {"master": "fb2611", "secondary": "fb2609"}}

    import app.services.contract_master as cm
    import app.services.kline_persist as kp

    monkeypatch.setattr(cm, "get_master_contracts", _masters)
    persisted: list[str] = []
    monkeypatch.setattr(
        kp,
        "persist_closed_bar",
        AsyncMock(side_effect=lambda symbol, bar: persisted.append(symbol) or 0),
    )

    quotes = [
        {"symbol": "fb2611", "last_price": 1269.0, "volume": 200, "position": 10},
        {"symbol": "fb2609", "last_price": 1172.0, "volume": 50, "position": 5},
    ]
    await kline.update_realtime_klines(r, quotes)

    assert persisted == ["fb2611"]  # 只有主力写 PG;次主力 fb2609 不写
    kline._realtime_state.clear()


async def test_persist_1m_allows_all_when_master_read_fails(monkeypatch) -> None:
    """主力集合读取失败时不限制(放行全部),不因守卫丢数据"""
    from unittest.mock import AsyncMock

    kline._realtime_state.clear()
    bj = timezone(timedelta(hours=8))
    kline._realtime_state["rb2610"] = {
        "1m": {
            "bar": _bar("2026-08-20 10:15:00", close=3100.0),
            "bar_time": "2026-08-20 10:15:00",
            "period_start_volume": 100,
            "open_locked": True,
        }
    }
    r = _FakeRedis()
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now: True)
    rolled = datetime(2026, 8, 20, 10, 16, 0, tzinfo=bj)
    monkeypatch.setattr(
        kline,
        "_get_period_start_time",
        lambda period, now, symbol="": rolled.replace(second=0),
    )
    monkeypatch.setattr(kline, "_is_minute_bar_in_session", lambda s: True)

    async def _boom(redis_conn):
        raise RuntimeError("redis down")

    import app.services.contract_master as cm
    import app.services.kline_persist as kp

    monkeypatch.setattr(cm, "get_master_contracts", _boom)
    persisted: list[str] = []
    monkeypatch.setattr(
        kp,
        "persist_closed_bar",
        AsyncMock(side_effect=lambda symbol, bar: persisted.append(symbol) or 0),
    )
    await kline.update_realtime_klines(
        r, [{"symbol": "rb2610", "last_price": 3101.0, "volume": 300, "position": 9}]
    )
    assert persisted == ["rb2610"]
    kline._realtime_state.clear()
