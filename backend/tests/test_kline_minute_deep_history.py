"""分钟深历史接回 /api/market/kline 链路测试（2026-08-23）

背景：新浪原生多分钟接口停用后，主链多分钟 = 新浪 1m（~1023 根）聚合，
60m≈17 / 30m≈34 / 15m≈68 根，低于因子实验室客户端 60 根门槛。
本文件覆盖两处接入：
1. 最新视图根数不足时从 pg-tickdata 补本合约更早段（前缀合并 + 写回缓存）；
2. 分钟多周期的近端（<30 天）end_time 回溯直连 tickdata（本合约自身 bar）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import kline

_BJ_TZ = timezone(timedelta(hours=8))


class _FakeRedis:
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
        "settle": None,
        "open_interest": None,
    }


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _hourly(n: int, end_hours_ago: float) -> list[dict[str, Any]]:
    """n 根 1 小时间隔 bar，最后一根在 end_hours_ago 小时前（全部在过去）"""
    last = datetime.now(_BJ_TZ).replace(tzinfo=None) - timedelta(hours=end_hours_ago)
    last = last.replace(minute=0, second=0, microsecond=0)
    return [_bar(_ts(last - timedelta(hours=i))) for i in range(n - 1, -1, -1)]


def _setup(monkeypatch, td_calls: list[tuple], td_return: Any = None) -> None:
    """统一环境：休市（stale gate 关）+ 无实时基准价（price gate 关）+

    mock fetch_tickdata_kline（记录调用、可编程返回）。
    本套件测深历史回填：bar 时间相对真实 now 构造，不保证落在交易时段，
    日历过滤置为恒等（日历过滤由 test_trading_calendar.py 覆盖）。
    """
    monkeypatch.setattr(kline, "is_trading_hours", lambda: False)
    monkeypatch.setattr(
        kline, "_filter_non_trading_bars", lambda bars, period: bars
    )
    # 闭市优先 pg-tickdata 分支与本套件无关（测深历史回填），置空
    async def _no_first(symbol, period, limit):
        return []

    monkeypatch.setattr(kline, "_tickdata_first_when_closed", _no_first)
    # 未来过滤按交易分钟轴判定，夹具 bar 相对真实 now 构造（凌晨/周末
    # 运行时会落在非交易轴上被误丢），置为恒等保持套件时间无关
    monkeypatch.setattr(
        kline, "_filter_bars_not_future",
        lambda bars, period, symbol="": bars,
    )

    async def _no_ref(redis_conn, symbol):
        return (0.0, None)

    monkeypatch.setattr(kline, "_live_ref_price", _no_ref)

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        td_calls.append((symbol, period, end_time, limit, adjusted))
        if isinstance(td_return, Exception):
            raise td_return
        return list(td_return or [])

    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)


# ========== 最新视图补深 ==========


async def test_latest_view_backfills_deep_history(monkeypatch):
    """主链只有 17 根 60m：从 tickdata 补更早段，前缀合并 + 写回缓存"""
    _setup(monkeypatch, td_calls := [], td_return := [])
    shallow = _hourly(17, 1)  # 主链浅序列：18 小时前 → 1 小时前
    deep = _hourly(500, 18)  # tickdata 深段：早于主链最早 bar
    td_return.extend(deep)

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return list(shallow)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    r = _FakeRedis()
    out = await kline.get_kline_history(r, "v2701", "60m", limit=0, session=None)

    assert len(out) == 517
    assert out[0]["time"] == deep[0]["time"]  # 前缀 = tickdata 深段
    assert out[-1]["time"] == shallow[-1]["time"]  # 尾部 = 主链（实时性）
    times = [b["time"] for b in out]
    assert times == sorted(times)  # 升序
    assert len(times) == len(set(times))  # 无重复
    # tickdata 调用参数：end_time = 主链最早 bar（只拉缺口），limit = 目标 500
    assert len(td_calls) == 1
    sym, period, end_time, limit, adjusted = td_calls[0]
    assert (sym, period) == ("v2701", "60m")
    assert end_time == shallow[0]["time"]
    assert limit == 500
    # 补全结果整体写回历史缓存
    cached = json.loads(r.store[kline._history_cache_key("v2701", "60m")])
    assert len(cached) == 517


async def test_backfill_honors_larger_request_limit(monkeypatch):
    """请求 limit=1500：补深目标跟随 limit，返回切到 1500 根"""
    _setup(monkeypatch, td_calls := [], td_return := [])
    shallow = _hourly(17, 1)
    deep = _hourly(1600, 18)
    td_return.extend(deep)

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return list(shallow)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(
        _FakeRedis(), "v2701", "60m", limit=1500, session=None
    )
    assert len(out) == 1500  # (1600+17)[-1500:]
    assert len(td_calls) == 1 and td_calls[0][3] == 1500  # limit 跟随请求


async def test_cached_shallow_backfilled_and_rewritten(monkeypatch):
    """缓存命中的浅序列（旧版本写入）同样补深并重写缓存"""
    _setup(monkeypatch, td_calls := [], td_return := [])
    shallow = _hourly(17, 1)
    deep = _hourly(500, 18)
    td_return.extend(deep)

    r = _FakeRedis(
        {kline._history_cache_key("v2701", "30m"): json.dumps(shallow)}
    )

    async def _should_not_call(*a, **k):
        raise AssertionError("缓存命中不应触发 provider 重拉")

    monkeypatch.setattr(kline, "_fetch_from_provider", _should_not_call)

    out = await kline.get_kline_history(r, "v2701", "30m", limit=0, session=None)
    assert len(out) == 517
    cached = json.loads(r.store[kline._history_cache_key("v2701", "30m")])
    assert len(cached) == 517  # 缓存已重写为完整序列


async def test_backfill_overlap_dedup_prefers_primary(monkeypatch):
    """补深层与主链重叠：按 time 去重，主链（实时）值覆盖"""
    _setup(monkeypatch, td_calls := [], td_return := [])
    # 主链 5 根；tickdata 返回 10 根，其中最后 3 根与主链前 3 根时间相同
    shallow = _hourly(5, 1)
    deep = _hourly(10, 3)  # 最后一根在 3 小时前 = 与主链第 3 根重叠区
    overlap = {b["time"] for b in shallow[:5]} & {b["time"] for b in deep}
    assert overlap  # 确有重叠
    for b in deep:
        b["close"] = 1111.0
    for b in shallow:
        b["close"] = 9999.0
    td_return.extend(deep)

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return list(shallow)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(_FakeRedis(), "v2701", "15m", limit=0, session=None)
    times = [b["time"] for b in out]
    assert len(times) == len(set(times))
    by_time = {b["time"]: b["close"] for b in out}
    for t in overlap:
        assert by_time[t] == 9999.0  # 主链覆盖 tickdata


async def test_backfill_empty_tickdata_keeps_original(monkeypatch):
    """tickdata 无该合约数据（返回空）：保持原序列，缓存原样"""
    _setup(monkeypatch, td_calls := [], [])

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return _hourly(17, 1)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    r = _FakeRedis()
    out = await kline.get_kline_history(r, "v2701", "60m", limit=0, session=None)
    assert len(out) == 17
    cached = json.loads(r.store[kline._history_cache_key("v2701", "60m")])
    assert len(cached) == 17


async def test_no_backfill_for_1m_and_1d(monkeypatch):
    """1m / 1d 不在补深范围：浅序列原样返回，不调 tickdata"""
    _setup(monkeypatch, td_calls := [], [])

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return _hourly(100, 1)  # 即便浅也不补

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    for period in ("1m", "1d"):
        out = await kline.get_kline_history(
            _FakeRedis(), "v2701", period, limit=0, session=None
        )
        # 1d 会进 _attach_realtime_bar（quote 无 → 原样），1m 直接返回
        assert 0 < len(out) <= 101
    assert td_calls == []


async def test_backfill_swallows_tickdata_exception(monkeypatch):
    """补深调用抛异常（无 .env 环境 Settings 校验失败等）：吞掉、原序列返回

    2026-08-23 33 部署测试容器（无 .env）实测：tickdata_kline._get_pool 的
    load_settings 在 try 外抛 ValidationError 曾炸穿补深路径。根因已在
    _get_pool 修复（配置异常=未配置），此处验证调用方 best-effort 防御。
    """
    _setup(monkeypatch, td_calls := [], [])

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        raise RuntimeError("Settings missing")

    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return _hourly(17, 1)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(_FakeRedis(), "v2701", "60m", limit=0, session=None)
    assert len(out) == 17


async def test_get_pool_swallows_settings_error(monkeypatch):
    """tickdata 连接池：Settings 校验异常等同未配置，绝不抛上层"""
    from app.data import tickdata_kline as td

    monkeypatch.setattr(td, "_pool", None)

    def _boom():
        raise RuntimeError("missing required settings")

    monkeypatch.setattr(td, "load_settings", _boom)
    assert await td._get_pool() is None


# ========== 近端 end_time 回溯直连 tickdata ==========


async def test_recent_minute_lookback_goes_tickdata_direct(monkeypatch):
    """60m 近端回溯（end_time 10 分钟前，< 30 天线）：先走 tickdata 本合约"""
    _setup(monkeypatch, td_calls := [], td_return := [])
    now = datetime.now(_BJ_TZ).replace(tzinfo=None)
    end_time = _ts(now - timedelta(minutes=10))
    seg = _hourly(50, 0.5)  # tickdata 本合约段
    td_return.extend(seg)

    async def _should_not_call(*a, **k):
        raise AssertionError("tickdata 命中后不应再走分发源主链")

    monkeypatch.setattr(kline, "_fetch_from_provider", _should_not_call)

    out = await kline.get_kline_history(
        _FakeRedis(), "v2701", "60m", limit=50, end_time=end_time, session=None
    )
    assert [b["time"] for b in out] == [b["time"] for b in seg]
    assert td_calls and td_calls[0][2] == end_time  # 直连带原 end_time


async def test_recent_minute_lookback_falls_back_when_tickdata_empty(monkeypatch):
    """tickdata 未命中（未配置/无该合约）：落回原主链路径不受影响"""
    _setup(monkeypatch, td_calls := [], [])
    now = datetime.now(_BJ_TZ).replace(tzinfo=None)
    end_time = _ts(now - timedelta(minutes=10))
    shallow = _hourly(17, 1)

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return list(shallow)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(
        _FakeRedis(), "v2701", "60m", limit=0, end_time=end_time, session=None
    )
    assert len(out) == 17  # 回退原路径：缓存切片返回主链浅序列


async def test_recent_daily_lookback_keeps_distributor_path(monkeypatch):
    """1d 近端回溯维持 30 天限制：不直连 tickdata，走主链"""
    _setup(monkeypatch, td_calls := [], [])

    async def _td_should_not_be_called(*a, **k):
        raise AssertionError("1d 近端回溯不应直连 tickdata")

    monkeypatch.setattr(kline, "fetch_tickdata_kline", _td_should_not_be_called)

    now = datetime.now(_BJ_TZ).replace(tzinfo=None)
    daily = [_bar((now - timedelta(days=d + 1)).strftime("%Y-%m-%d")) for d in range(3)]

    async def _fake_provider(redis_conn, symbol, period, session=None, is_stale=None):
        return list(daily)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(
        _FakeRedis(),
        "v2701",
        "1d",
        limit=0,
        end_time=_ts(now - timedelta(minutes=10)),
        session=None,
    )
    assert [b["time"] for b in out] == [b["time"] for b in daily]
