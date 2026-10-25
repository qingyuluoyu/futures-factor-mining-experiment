"""永久历史读写：闭市优先 pg-tickdata + 权威修正沉淀

2026-08-24 用户诉求：vvtr 补全应沉淀进 pg-tickdata 永久历史，读取
（至少闭市时）优先从永久库拿，Redis 过期后不再因 sina 限流 / vvtr
残缺而"丢历史"（pp2701 日线缺 8-11~8-21 实例）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import kline, kline_sync

_BJ = timezone(timedelta(hours=8))


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
    }


# ---------- 闭市尾部新鲜度 ----------


def test_closed_market_stale_daily_grace() -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=_BJ)  # 周一
    fresh = [_bar("2026-08-21")]  # 周五收盘，2 天前
    stale = [_bar("2026-08-15")]  # 9 天前（未被同步覆盖）
    assert kline._closed_market_stale(fresh, "1d", now) is False
    assert kline._closed_market_stale(stale, "1d", now) is True


def test_closed_market_stale_minute_grace() -> None:
    now = datetime(2026, 8, 24, 12, 0, tzinfo=_BJ)  # 周一
    fresh = [_bar("2026-08-22 01:00:00")]  # 周五夜盘尾巴，约 2.5 天前
    stale = [_bar("2026-08-19 22:00:00")]  # 上周四，超 3.6 天
    assert kline._closed_market_stale(fresh, "5m", now) is False
    assert kline._closed_market_stale(stale, "5m", now) is True
    assert kline._closed_market_stale([], "5m", now) is True


# ---------- get_kline_history 闭市优先 pg-tickdata ----------


def _closed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """闭市 + 无实时基准价 + 过滤器恒等（夹具 now 相对时间，套件时间无关）"""
    monkeypatch.setattr(kline, "is_trading_hours", lambda: False)
    monkeypatch.setattr(
        kline, "_filter_non_trading_bars", lambda bars, period: bars
    )
    monkeypatch.setattr(
        kline, "_filter_bars_not_future", lambda bars, period, symbol="": bars
    )

    async def _no_ref(redis_conn, symbol):
        return (0.0, None)

    monkeypatch.setattr(kline, "_live_ref_price", _no_ref)

    async def _no_attach(redis_conn, symbol, period, bars, trading_mode="live"):
        return bars

    monkeypatch.setattr(kline, "_attach_realtime_bar", _no_attach)


@pytest.mark.asyncio
async def test_closed_market_prefers_tickdata_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """闭市 + 缓存 miss：优先 pg-tickdata，不打 HTTP 源链。"""
    _closed_env(monkeypatch)
    td_bars = [
        _bar("2026-08-20 10:00:00", 3000.0),
        _bar("2026-08-21 10:00:00", 3010.0),
    ]

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        assert end_time == "", "闭市优先分支应请求最新视图"
        return list(td_bars)

    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)
    monkeypatch.setattr(
        kline, "_closed_market_stale", lambda bars, period, now=None: False
    )

    async def _should_not_call(*a, **k):
        raise AssertionError("tickdata 命中后闭市不应再走 HTTP 源链")

    monkeypatch.setattr(kline, "_fetch_from_provider", _should_not_call)

    r = _FakeRedis()
    out = await kline.get_kline_history(r, "pp2701", "1d", limit=10, session=None)
    assert [b["close"] for b in out] == [3000.0, 3010.0]
    # 命中后写入历史缓存（TTL 内后续请求直接命中）
    assert kline._history_cache_key("pp2701", "1d") in r.store


@pytest.mark.asyncio
async def test_closed_market_stale_tickdata_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """永久库尾部过旧（合约未被同步覆盖）：落回 HTTP 源链。"""
    _closed_env(monkeypatch)
    td_bars = [_bar("2026-08-01 10:00:00", 3000.0)]  # 过旧

    async def _fake_td(symbol, period, end_time="", limit=0, adjusted=False):
        return list(td_bars)

    monkeypatch.setattr(kline, "fetch_tickdata_kline", _fake_td)
    monkeypatch.setattr(
        kline, "_closed_market_stale", lambda bars, period, now=None: True
    )

    http_bars = [_bar("2026-08-21 10:00:00", 3010.0)]

    async def _fake_provider(*a, **k):
        return list(http_bars)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    r = _FakeRedis()
    out = await kline.get_kline_history(r, "pp2701", "1d", limit=10, session=None)
    assert [b["close"] for b in out] == [3010.0]


# ---------- 权威修正沉淀 pg-tickdata ----------


@pytest.mark.asyncio
async def test_attach_daily_forming_trading_day_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """交易日 T 的日 K 从其首个交易段（夜盘）开始就存在。

    - 有夜盘（pp）：周一 08:48 盘前应挂 08-24（周五 21:00 夜盘属 08-24，
      内容=夜盘定格）——2026-08-24 用户实测反馈场景
    - 无夜盘（if 股指）：周日晚不预埋次日 bar（次日 09:00 未开始）
    - 乱序重放帧（偏离上一交易日收盘 >8%）不挂
    """
    from datetime import datetime as _dt

    class _Clock:
        fixed: datetime

        @classmethod
        def now(cls, tz=None):
            base = cls.fixed
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)

        strptime = staticmethod(_dt.strptime)

    async def _quote(redis_conn, symbol):
        return {"last_price": 8142.0, "open_price": 8148.0,
                "high_price": 8154.0, "low_price": 8085.0, "volume": 206347}

    monkeypatch.setattr(kline, "_load_live_daily_quote", _quote)
    monkeypatch.setattr(kline, "datetime", _Clock)
    friday = [_bar("2026-08-21", 8121.0)]

    # 周一 08:48（盘前）：pp 有夜盘，08-24 首段=周五 21:00 已开始 → 挂
    _Clock.fixed = datetime(2026, 8, 24, 8, 48, tzinfo=_BJ)
    out = await kline._attach_realtime_bar(None, "pp2701", "1d", list(friday))
    assert [b["time"] for b in out] == ["2026-08-21", "2026-08-24"]
    assert out[-1]["open"] == 8148.0  # 周五夜盘开盘价

    # 周日 22:00：pp 的 08-24 首段（周五夜盘）已过 → 保留挂载（定格内容正确）
    _Clock.fixed = datetime(2026, 8, 23, 22, 0, tzinfo=_BJ)
    out2 = await kline._attach_realtime_bar(None, "pp2701", "1d", list(friday))
    assert [b["time"] for b in out2] == ["2026-08-21", "2026-08-24"]

    # 周日 22:00：if 股指无夜盘，08-24 首段=周一 09:00 未开始 → 不挂
    _Clock.fixed = datetime(2026, 8, 23, 22, 0, tzinfo=_BJ)
    out3 = await kline._attach_realtime_bar(None, "if2609", "1d", list(friday))
    assert [b["time"] for b in out3] == ["2026-08-21"]

    # 乱序重放帧：quote 价格与上一交易日收盘偏离 >8% → 不挂
    async def _bad_quote(redis_conn, symbol):
        return {"last_price": 7000.0, "open_price": 7000.0,
                "high_price": 7000.0, "low_price": 6990.0, "volume": 100}
    monkeypatch.setattr(kline, "_load_live_daily_quote", _bad_quote)
    _Clock.fixed = datetime(2026, 8, 24, 8, 48, tzinfo=_BJ)
    out4 = await kline._attach_realtime_bar(None, "pp2701", "1d", list(friday))
    assert [b["time"] for b in out4] == ["2026-08-21"]


class _FakeSessionFactory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self):
        self.calls.append("open")
        return self

    async def __aenter__(self):
        return object()  # session 占位（upsert 已 mock）

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_sync_one_period_reads_tickdata_no_writeback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两段式第二段：从 pg-tickdata 读权威 bar 修正主 PG，不回写 tickdata。"""
    bars = [_bar("2026-08-21 10:00:00", 8121.0)]

    async def _fake_fetch_tickdata(symbol, period, end_time="", limit=0, adjusted=False):
        assert (symbol, period) == ("pp2701", "5m")
        return list(bars)

    monkeypatch.setattr(
        "app.data.tickdata_kline.fetch_tickdata_kline", _fake_fetch_tickdata
    )

    async def _fake_upsert_minute(session, code, symbol, period, in_bars):
        return len(in_bars)

    monkeypatch.setattr(
        "app.services.kline_minute.upsert_minute_bars_update",
        _fake_upsert_minute,
    )

    persisted: list[tuple] = []

    async def _fake_persist(symbol, period, in_bars, force=False):
        persisted.append((symbol, period, in_bars, force))
        return len(in_bars)

    monkeypatch.setattr(
        "app.data.tickdata_writer.persist_bars", _fake_persist
    )

    n, stats = await kline_sync._sync_one_period(
        _FakeSessionFactory(), "pp", "pp2701", "5m", redis_conn=None
    )
    assert n == 1
    assert stats["verified"] == 1
    # 数据本就来自 pg-tickdata：不回写（避免无谓重写共享库）
    assert persisted == []


# ---------- 主 PG 固化 1m 差集沉淀永久库 ----------


@pytest.mark.asyncio
async def test_forming_relax_is_bounded_not_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """forming 新鲜只把 stale gate 放宽到 3×宽限，不是完全关闭。

    2026-08-24 pp2701 实例：尾部滞后 14 天的残缺日线缓存（08-10）在
    forming 掩护下被直接采用 → 中段 08-11~08-21 丢失。放宽后该缓存
    仍被拒并触发重拉。
    """
    monkeypatch.setattr(kline, "is_trading_hours", lambda: True)
    monkeypatch.setattr(kline, "is_symbol_trading", lambda symbol, now=None: True)
    # 屏蔽夜盘"缺当日闭合"下限（专项见 test_kline_daily_night_gap.py）：
    # 本测试聚焦放宽有界性；floor 依赖真实时钟，夜盘时段跑会拒掉尾 08-21
    monkeypatch.setattr(kline, "_daily_closed_floor", lambda now_bj: None)

    async def _fresh_forming(redis_conn, mode, symbol, period, max_age):
        return True

    monkeypatch.setattr(kline, "_forming_bar_fresh", _fresh_forming)
    monkeypatch.setattr(
        kline, "_filter_non_trading_bars", lambda bars, period: bars
    )
    monkeypatch.setattr(
        kline, "_filter_bars_not_future", lambda bars, period, symbol="": bars
    )

    async def _no_ref(redis_conn, symbol):
        return (0.0, None)

    monkeypatch.setattr(kline, "_live_ref_price", _no_ref)

    async def _no_attach(redis_conn, symbol, period, bars, trading_mode="live"):
        return bars

    monkeypatch.setattr(kline, "_attach_realtime_bar", _no_attach)

    # 夹具日期相对 now 构造（曾用固定 2026-08-10/08-21，真时间推进 12 天
    # 放宽界后 provider 尾必拒——时间炸弹，2026-09-02 卡 33 部署实测）。
    now = datetime.now(_BJ).replace(tzinfo=None)
    tail = now - timedelta(days=3)  # provider 尾：4 天宽限内
    cache_tail = now - timedelta(days=14)  # 缓存尾：超 3×4=12 天放宽界
    # 缓存：长序列但尾部滞后 14 天
    cache = [
        _bar(f"{cache_tail - timedelta(days=2):%Y-%m-%d}", 8000.0),
        _bar(f"{cache_tail:%Y-%m-%d}", 8010.0),
    ]
    r = _FakeRedis({kline._history_cache_key("pp2701", "1d"): json.dumps(cache)})

    # provider 返回完整尾（tail-1 与 tail 两根）
    http = [
        _bar(f"{tail - timedelta(days=1):%Y-%m-%d}", 7994.0),
        _bar(f"{tail:%Y-%m-%d}", 8121.0),
    ]

    async def _fake_provider(*a, **k):
        return list(http)

    monkeypatch.setattr(kline, "_fetch_from_provider", _fake_provider)

    out = await kline.get_kline_history(r, "pp2701", "1d", limit=10, session=None)
    # 残缺缓存被拒，采用 provider 完整尾（旧逻辑放宽=None 会返回缓存尾）
    assert [b["time"] for b in out] == [
        f"{tail - timedelta(days=1):%Y-%m-%d}", f"{tail:%Y-%m-%d}"
    ]


class _FakeConn:
    def __init__(self, fetch_rows):
        self._rows = fetch_rows

    async def fetch(self, *a, **k):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, fetch_rows):
        self._conn = _FakeConn(fetch_rows)

    def acquire(self):
        return self._conn


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSessionPG:
    """session 工厂：() → 异步上下文 → 可 execute 的 session"""

    def __init__(self, rows):
        self._rows = rows

    def __call__(self):
        rows = self._rows

        class _CM:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, *a, **k):
                return _FakeResult(rows)

        return _CM()


@pytest.mark.asyncio
async def test_backfill_1m_fills_gap_and_filters_weekend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主 PG 固化比永久库新 → 差集补入；周末幽灵不进永久库。

    场景复刻 pp2701 事故：pg-tickdata 1m 尾部 eob=08-21 21:16（bob 21:15），
    主 PG 有周五夜盘全段 + 周日幽灵。
    """
    # 主 PG 行：(symbol, ts_bj, o, h, l, c, vol, hold)
    main_rows = [
        ("pp2701", "2026-08-21 21:14:00", 8100, 8101, 8099, 8100, 100, 10),
        ("pp2701", "2026-08-21 21:16:00", 8100, 8102, 8098, 8101, 110, 11),
        ("pp2701", "2026-08-21 22:59:00", 8141, 8142, 8140, 8142, 120, 12),
        ("pp2701", "2026-08-23 22:00:00", 8142, 8142, 8142, 8142, 0, 12),  # 周日幽灵
    ]
    # pg-tickdata 现状：instrument → max eob（bob 21:15 → eob 21:16）
    td_rows = [("pp2701", "2026-08-21 21:16:00")]

    async def _fake_pool():
        return _FakePool(td_rows)

    monkeypatch.setattr("app.data.tickdata_kline._get_pool", _fake_pool)

    persisted: list[tuple[str, str, list, bool]] = []

    async def _fake_persist(symbol, period, in_bars, force=False):
        persisted.append((symbol, period, in_bars, force))
        return len(in_bars)

    monkeypatch.setattr(
        "app.data.tickdata_writer.persist_bars", _fake_persist
    )

    out = await kline_sync.backfill_tickdata_1m_from_mainpg(
        _FakeSessionPG(main_rows)
    )
    assert out == {"pp2701": 3}
    sym, per, in_bars, force = persisted[0]
    assert (sym, per, force) == ("pp2701", "1m", True)
    times = [b["time"] for b in in_bars]
    # 21:14 在重写窗口内（覆盖修正），21:16/22:59 是缺口补入，
    # 周日幽灵被日历过滤剔除
    assert times == [
        "2026-08-21 21:14:00",
        "2026-08-21 21:16:00",
        "2026-08-21 22:59:00",
    ]


@pytest.mark.asyncio
async def test_backfill_1m_no_pool_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_pool():
        return None

    monkeypatch.setattr("app.data.tickdata_kline._get_pool", _no_pool)
    out = await kline_sync.backfill_tickdata_1m_from_mainpg(_FakeSessionPG([]))
    assert out == {}
