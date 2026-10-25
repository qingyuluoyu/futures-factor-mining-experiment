"""最新视图 K 线新鲜度守卫测试

交易时段内（live + end_time 空 = 切品种首屏 / AI 决策取数），
任何来源（缓存 / HTTP 源 / PG / TickData）的最新 bar 过旧都不得
作为最新行情返回——旧 K 线会画到图上并喂给 AI 交易引擎导致误判。
非交易时段与历史回溯（end_time 非空）不受影响。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.data import kline_provider as kp
from app.services import kline

_BJ_TZ = timezone(timedelta(hours=8))


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


def _now_str(minutes_ago: float = 0) -> str:
    t = datetime.now(_BJ_TZ).replace(tzinfo=None) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _trading_on(monkeypatch) -> None:
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now=None: True)
    # 本套件测守卫逻辑：bar 时间相对真实 now 构造，不保证落在交易时段，
    # 日历过滤置为恒等（日历过滤有自己的 test_trading_calendar.py 覆盖）
    monkeypatch.setattr(
        kline, "_filter_non_trading_bars", lambda bars, period: bars
    )


def _trading_off(monkeypatch) -> None:
    monkeypatch.setattr(kline, "is_trading_hours", lambda: False)
    monkeypatch.setattr(
        kline, "_filter_non_trading_bars", lambda bars, period: bars
    )


def _wall_clock_axis(monkeypatch) -> None:
    """把多分钟周期终点改为墙钟网格对齐。

    真实的 period_end_for_product 按交易分钟轴计算（茶歇/午休不计分钟），
    在茶歇时段运行测试时会把临近墙钟的测试 bar 误判为「未来 bar」滤掉。
    这里与测试 bar 的墙钟时间对齐，保证任何时刻运行都稳定。
    """

    def _fake_axis(code, at, minutes):
        base = at.replace(second=0, microsecond=0)
        delta = (at.minute // minutes + 1) * minutes - at.minute
        return base + timedelta(minutes=delta)

    monkeypatch.setattr(kline, "period_end_for_product", _fake_axis)


# ========== 守卫构造 ==========


def test_gate_none_when_not_trading(monkeypatch):
    _trading_off(monkeypatch)
    assert kline._build_latest_stale_gate("5m", "rb2610") is None


def test_gate_none_when_symbol_closed(monkeypatch):
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now=None: False)
    assert kline._build_latest_stale_gate("5m", "rb2610") is None


def test_gate_minute_threshold(monkeypatch):
    _trading_on(monkeypatch)
    gate = kline._build_latest_stale_gate("5m", "rb2610")
    assert gate is not None
    # 5m：2×5+15=25 分钟阈值内 → 新鲜
    assert gate([_bar(_now_str(10))]) is False
    # 两天前 → 过期（PG/TickData 兜底的典型旧数据）
    assert gate([_bar(_now_str(2 * 24 * 60))]) is True
    # 空 / 时间不可解析 → 不拦截（防御性）
    assert gate([]) is False
    assert gate([_bar("")]) is False


def test_gate_daily_threshold(monkeypatch):
    _trading_on(monkeypatch)
    # 屏蔽夜盘"缺当日闭合"下限（专项见 test_kline_daily_night_gap.py），
    # 本测试聚焦 4 自然日宽限本身；floor 依赖真实时钟会在夜盘时段跑挂
    monkeypatch.setattr(kline, "_daily_closed_floor", lambda now_bj: None)
    gate = kline._build_latest_stale_gate("1d", "rb2610")
    assert gate is not None
    # 周一/周二盘中仍可用的周五日线（3 天）→ 新鲜
    assert gate([_bar(_now_str(3 * 24 * 60))]) is False
    # 5 天前 → 过期
    assert gate([_bar(_now_str(5 * 24 * 60))]) is True


# ========== provider 逐源跳过 ==========


async def test_provider_skips_stale_source(monkeypatch):
    stale = [_bar("2020-01-06 10:35:00")]
    fresh = [_bar(_now_str(3))]
    calls: list[str] = []

    def _stale_fetcher(symbol, period):
        calls.append("sina-primary")
        return stale

    def _fresh_fetcher(symbol, period):
        calls.append("simnow")
        return fresh

    monkeypatch.setattr(kp, "_register_fetchers", lambda *a, **k: None)
    kp._SOURCE_FETCHERS.clear()
    kp._SOURCE_FETCHERS["sina"] = _stale_fetcher
    kp._SOURCE_FETCHERS["simnow"] = _fresh_fetcher

    def _is_stale(bars: list[dict[str, Any]]) -> bool:
        return bool(bars) and str(bars[-1].get("time") or "").startswith("2020")

    out = await kp.fetch_kline_with_fallback(
        None, "rb2610", "5m",
        primary="sina", backups=["simnow"], is_stale=_is_stale,
    )
    assert calls == ["sina-primary", "simnow"]
    assert out == fresh


async def test_provider_without_gate_keeps_first_source(monkeypatch):
    """不传 is_stale（虚拟盘种子等旧调用）：行为不变，第一成功源直接返回"""
    stale = [_bar("2020-01-06 10:35:00")]
    monkeypatch.setattr(kp, "_register_fetchers", lambda *a, **k: None)
    kp._SOURCE_FETCHERS.clear()
    kp._SOURCE_FETCHERS["sina"] = lambda s, p: stale
    kp._SOURCE_FETCHERS["simnow"] = lambda s, p: [_bar(_now_str())]

    out = await kp.fetch_kline_with_fallback(
        None, "rb2610", "5m", primary="sina", backups=["simnow"]
    )
    assert out == stale


# ========== get_kline_history 最新视图 ==========


async def test_all_sources_stale_returns_empty(monkeypatch):
    """交易时段内全源过期：返回空 + 负缓存，旧数据不进历史缓存"""
    _trading_on(monkeypatch)
    stale = [_bar(_now_str(3 * 24 * 60))]
    r = _FakeRedis()

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        assert is_stale is not None  # 最新视图必须带守卫
        assert is_stale(stale) is True
        return stale

    async def _fake_pg(session, symbol, period):
        return stale

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        return stale

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)
    monkeypatch.setattr(kline, "_fetch_from_pg", _fake_pg)
    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)

    out = await kline.get_kline_history(
        r, "rb2610", "5m", limit=100, session=object()
    )
    assert out == []
    # 负缓存已写；历史缓存未写（旧数据绝不冒充最新行情）
    assert r.store.get(kline._history_cache_key("rb2610", "5m")) is None
    assert r.store.get("kline:history:fail:rb2610:5m") == "1"


async def test_stale_provider_falls_to_fresh_pg(monkeypatch):
    _wall_clock_axis(monkeypatch)
    """实时源过期但 PG 兜底足够新：允许用 PG，并照常写缓存"""
    _trading_on(monkeypatch)
    stale = [_bar(_now_str(3 * 24 * 60))]
    fresh_pg = [_bar(_now_str(6))]
    r = _FakeRedis()

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return stale

    async def _fake_pg(session, symbol, period):
        return fresh_pg

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)
    monkeypatch.setattr(kline, "_fetch_from_pg", _fake_pg)

    out = await kline.get_kline_history(
        r, "rb2610", "5m", limit=100, session=object()
    )
    assert [b["time"] for b in out] == [fresh_pg[0]["time"]]
    # 新数据才允许进缓存
    cached = json.loads(r.store[kline._history_cache_key("rb2610", "5m")])
    assert [b["time"] for b in cached] == [fresh_pg[0]["time"]]


async def test_stale_cache_ignored_and_refetched(monkeypatch):
    _wall_clock_axis(monkeypatch)
    """缓存里的过期兜底数据（旧版本写入）不能继续当最新行情"""
    _trading_on(monkeypatch)
    stale = [_bar(_now_str(3 * 24 * 60))]
    fresh = [_bar(_now_str(4))]
    r = _FakeRedis(
        {kline._history_cache_key("rb2610", "5m"): json.dumps(stale)}
    )

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        assert is_stale is not None and is_stale(stale) is True
        return fresh

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(r, "rb2610", "5m", limit=100, session=None)
    assert [b["time"] for b in out] == [fresh[0]["time"]]


async def test_fresh_cache_served_without_refetch(monkeypatch):
    _wall_clock_axis(monkeypatch)
    _trading_on(monkeypatch)
    fresh = [_bar(_now_str(4))]
    r = _FakeRedis(
        {kline._history_cache_key("rb2610", "5m"): json.dumps(fresh)}
    )

    async def _should_not_call(*a, **k):
        raise AssertionError("新鲜缓存不应触发重拉")

    monkeypatch.setattr(kline, "_fetch_from_provider", _should_not_call)

    out = await kline.get_kline_history(r, "rb2610", "5m", limit=100, session=None)
    assert [b["time"] for b in out] == [fresh[0]["time"]]


async def test_no_gate_when_market_closed(monkeypatch):
    """休市时段：上一交易段数据就是最新，兜底照常返回"""
    _trading_off(monkeypatch)
    stale = [_bar(_now_str(3 * 24 * 60))]
    r = _FakeRedis()

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        assert is_stale is None
        return []

    async def _fake_pg(session, symbol, period):
        return stale

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        return []

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)
    monkeypatch.setattr(kline, "_fetch_from_pg", _fake_pg)
    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)

    out = await kline.get_kline_history(
        r, "rb2610", "5m", limit=100, session=object()
    )
    assert [b["time"] for b in out] == [stale[0]["time"]]


async def test_no_gate_for_history_lookback(monkeypatch):
    """历史回溯（end_time 非空）：面向过去时段，不做新鲜度拦截"""
    _trading_on(monkeypatch)
    old_bars = [
        _bar("2026-01-09 10:30:00"),
        _bar("2026-01-09 10:35:00"),
    ]
    r = _FakeRedis()

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        if end_time:
            return []  # 顶部 tickdata 回溯分支放空，落到 provider
        return []

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        assert is_stale is None  # 历史请求不携带守卫
        return old_bars

    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)
    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(
        r, "rb2610", "5m", limit=100,
        end_time="2026-01-10 23:59:59", session=None,
    )
    assert [b["time"] for b in out] == ["2026-01-09 10:30:00", "2026-01-09 10:35:00"]
