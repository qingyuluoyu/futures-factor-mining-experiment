"""K 线最新视图修复三件套测试

1. tickdata 最新视图禁止主力拼接（跨合约价格污染根因）
2. stale gate 实时 forming 放宽（vvtr 源尾部滞后不整源拒绝）
3. vvtr 当日段拆早晚两窗（上游时间格式长窗翻页丢尾部）

背景：2026-08-21 ma2701 15m 被 ma2609（当前主力）价格污染——
tickdata 兜底把 9999 连续段拼进最新视图，时间戳新鲜、价格错合约。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.data import tickdata_kline as td
from app.connectors.vvtr import http_client as vc
from app.services import kline

_BJ = timezone(timedelta(hours=8))


def _rows(times: list[str], price: float = 1.0) -> list[dict]:
    out = []
    for t in times:
        dt = datetime.fromisoformat(t)
        out.append({
            "ts": dt, "open": price, "high": price, "low": price,
            "close": price, "volume": 1, "position": 1,
        })
    return out


def _weekday_on_or_before(d: datetime) -> datetime:
    """回退到最近的工作日（夹具时刻须落在真实交易时段内，否则会被
    交易分钟轴归一折叠）。"""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# ========== 1. tickdata 最新视图禁拼接 ==========

def test_latest_view_never_stitches_continuous(monkeypatch):
    """end_time 空（最新视图）：本合约 bar 再少也不得拼 9999 主力段

    夹具 ts 为 pg-tickdata 真实口径的 eob（周期终点）标签：15m eob
    09:15/09:30 → 读取换算为 bob 09:00/09:15。

    夹具日期相对 now 构造（落在一周新鲜度宽限内的工作日交易时段）——
    曾用固定 2026-08-21，真时间推进 7 天宽限期后守卫必拒（时间炸弹）。
    """
    now = datetime.now(_BJ).replace(tzinfo=None)
    d = _weekday_on_or_before(now - timedelta(days=1))
    # 桩数据按 _query_tickdata_rows 契约给 DESC（最新在前）
    own = _rows(
        [f"{d:%Y-%m-%d} 09:30:00", f"{d:%Y-%m-%d} 09:15:00"], price=2700.0
    )
    cont = _rows(
        [f"{d:%Y-%m-%d} 13:45:00", f"{d:%Y-%m-%d} 10:00:00", f"{d:%Y-%m-%d} 09:45:00"],
        price=2888.0,  # ma2609 价格水平
    )

    async def fake_query(pool, pg_period, instrument, start, end, n):
        return cont if instrument.endswith("9999") else own

    monkeypatch.setattr(td, "_query_tickdata_rows", fake_query)

    async def fake_pool():
        return object()

    monkeypatch.setattr(td, "_get_pool", fake_pool)
    out = asyncio.run(td.fetch_tickdata_kline("ma2701", "15m", end_time="", limit=50))
    # 只含本合约 2 根；绝不允许 2888 价格（ma2609）混入
    assert len(out) == 2
    assert all(b["close"] == 2700.0 for b in out)
    assert [b["time"] for b in out] == [
        f"{d:%Y-%m-%d} 09:00:00",
        f"{d:%Y-%m-%d} 09:15:00",
    ]


def test_recent_backtrack_stitches_for_pagination(monkeypatch):
    """end_time 在最近月内（5 天前）：游标回溯放开 9999 拼接（2026-09-02）

    旧 30 天锚点门限使近端游标只回本合约自身 bar，own < limit 时上层
    has_more=false → 桌面端分页回溯在本合约段头断链（rb2701 60m
    449<500 即断）。新语义：带游标即允许拼接补页；最新视图（end_time
    空）仍由 test_latest_view_never_stitches_continuous 守住跨合约污染。
    """
    now = datetime.now(_BJ).replace(tzinfo=None)
    end = now - timedelta(days=5)
    # 守卫：近期锚定要求最新 bar 距锚点 ≤7 天 → 自身数据放锚点前 1 个工作日
    d = _weekday_on_or_before(end - timedelta(days=1))
    own = _rows([
        f"{d:%Y-%m-%d} 09:15:00",
        f"{d:%Y-%m-%d} 09:30:00",
    ], price=2700.0)
    cont = _rows([
        f"{d:%Y-%m-%d} 09:45:00",
        f"{d:%Y-%m-%d} 10:00:00",
    ], price=2888.0)

    async def fake_query(pool, pg_period, instrument, start, end_, n):
        return cont if instrument.endswith("9999") else own

    monkeypatch.setattr(td, "_query_tickdata_rows", fake_query)

    async def fake_pool():
        return object()

    monkeypatch.setattr(td, "_get_pool", fake_pool)
    out = asyncio.run(td.fetch_tickdata_kline(
        "ma2701", "15m", end_time=end.strftime("%Y-%m-%d %H:%M:%S"), limit=50))
    # 本合约 2 根 + 主力段补页 2 根（翻页不再断链）
    assert len(out) == 4
    assert sorted(b["close"] for b in out) == [2700.0, 2700.0, 2888.0, 2888.0]


def test_old_backtrack_still_stitches(monkeypatch):
    """end_time 早于 30 天（40 天前）：上市前缺口的 9999 拼接行为保留"""
    now = datetime.now(_BJ).replace(tzinfo=None)
    end = now - timedelta(days=40)
    d = _weekday_on_or_before(end - timedelta(days=1))
    own = _rows([
        f"{d:%Y-%m-%d} 09:15:00",
        f"{d:%Y-%m-%d} 09:30:00",
    ])
    cont = _rows([
        f"{d:%Y-%m-%d} 09:45:00",
        f"{d:%Y-%m-%d} 10:00:00",
    ])

    async def fake_query(pool, pg_period, instrument, start, end_, n):
        return cont if instrument.endswith("9999") else own

    monkeypatch.setattr(td, "_query_tickdata_rows", fake_query)

    async def fake_pool():
        return object()

    monkeypatch.setattr(td, "_get_pool", fake_pool)
    out = asyncio.run(td.fetch_tickdata_kline(
        "ma2701", "15m", end_time=end.strftime("%Y-%m-%d %H:%M:%S"), limit=50))
    assert len(out) == 4  # 本合约 + 主力连续拼接
    assert len({b["time"] for b in out}) == 4  # bob 换算后键不重叠


# ========== 1b. 最近月回溯走分发源链（不直连 tickdata） ==========

class _NullRedis:
    """带存储的假 Redis（get/set 记录到 _store）"""

    def __init__(self, store: dict | None = None):
        self._store: dict = dict(store or {})

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None):
        self._store[key] = value


def test_recent_backtrack_skips_tickdata_direct(monkeypatch):
    """end_time 在最近月内：1m/1d 不调 tickdata 直连，走 provider

    分钟多周期（5/15/30/60m）2026-08-23 起放开近端直连（本合约自身 bar +
    新鲜度守卫，见 test_kline_minute_deep_history.py）；本守卫对 1m/1d 保留。
    """
    from app.services import kline as ksvc

    now = datetime.now(_BJ).replace(tzinfo=None)
    end = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    called = {"tickdata": 0}

    async def fake_tickdata(*a, **k):
        called["tickdata"] += 1
        return []

    async def fake_provider(redis, symbol, period, session=None, is_stale=None):
        return [{
            "time": (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
        }]

    monkeypatch.setattr(ksvc, "fetch_tickdata_kline", fake_tickdata)
    monkeypatch.setattr(ksvc, "_fetch_from_provider", fake_provider)
    monkeypatch.setattr(ksvc, "is_trading_hours", lambda: False)
    # 夹具时间相对真实 now 构造，非交易时段运行会被日历/未来过滤误丢
    monkeypatch.setattr(
        ksvc, "_filter_bars_not_future", lambda bars, period, symbol="": bars
    )
    monkeypatch.setattr(
        ksvc, "_filter_non_trading_bars", lambda bars, period: bars
    )
    out = asyncio.run(ksvc.get_kline_history(
        _NullRedis(), "ma2701", "1m", limit=10, end_time=end))
    assert called["tickdata"] == 0  # 最近月不直连库
    assert len(out) == 1  # provider 数据正常返回


def test_old_backtrack_uses_tickdata_direct(monkeypatch):
    """end_time 早于最近月：tickdata 直连优先（长回溯既有行为）"""
    from app.services import kline as ksvc

    now = datetime.now(_BJ).replace(tzinfo=None)
    end = (now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    called = {"tickdata": 0}

    async def fake_tickdata(*a, **k):
        called["tickdata"] += 1
        return [{
            "time": (now - timedelta(days=41)).strftime("%Y-%m-%d %H:%M:%S"),
            "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2,
        }]

    monkeypatch.setattr(ksvc, "fetch_tickdata_kline", fake_tickdata)
    monkeypatch.setattr(ksvc, "is_trading_hours", lambda: False)
    out = asyncio.run(ksvc.get_kline_history(
        _NullRedis(), "ma2701", "15m", limit=10, end_time=end))
    assert called["tickdata"] == 1
    assert len(out) == 1 and out[0]["close"] == 2


# ========== 2. stale gate forming 放宽 ==========

class _FakeRedis:
    def __init__(self, payload: dict | None):
        self._payload = payload

    async def get(self, key: str):
        if self._payload is None:
            return None
        return json.dumps(self._payload)


def test_forming_fresh_relaxes_gate():
    now = datetime.now(_BJ).replace(tzinfo=None)
    fresh = _FakeRedis({
        "bar": {"time": (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")},
    })
    assert asyncio.run(kline._forming_bar_fresh(
        fresh, "live", "ma2701", "15m", timedelta(minutes=45))) is True

    stale = _FakeRedis({
        "bar": {"time": (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")},
    })
    assert asyncio.run(kline._forming_bar_fresh(
        stale, "live", "ma2701", "15m", timedelta(minutes=45))) is False

    assert asyncio.run(kline._forming_bar_fresh(
        _FakeRedis(None), "live", "ma2701", "15m", timedelta(minutes=45))) is False
    # 损坏载荷不抛错
    assert asyncio.run(kline._forming_bar_fresh(
        _FakeRedis({"unexpected": 1}), "live", "ma2701", "15m",
        timedelta(minutes=45))) is False


def test_latest_max_age_thresholds():
    assert kline._latest_max_age("15m") == timedelta(minutes=45)
    assert kline._latest_max_age("1d") == timedelta(days=4)
    assert kline._latest_max_age("tick") is None


# ========== 3. vvtr 当日早晚两窗 ==========

def test_vvtr_minute_fetch_splits_today_windows(monkeypatch):
    """分钟取数：历史段（日期）+ 当日早段 + 近段，且两窗连续无缺口"""
    windows: list[tuple[str, str]] = []

    def fake_window(api_key, base, sym, interval, f, t, limit):
        # 按调用次序区分窗口角色：#1 hist #2 am #3 pm
        role = ("hist", "am", "pm")[min(len(windows), 2)]
        windows.append((f, t))
        bars_by_role = {
            "hist": [{"time": "2026-08-20 22:45:00", "open": 3, "high": 3,
                      "low": 3, "close": 3, "volume": 3}],
            "am": [{"time": "2026-08-21 09:00:00", "open": 1, "high": 1,
                    "low": 1, "close": 1, "volume": 1}],
            "pm": [{"time": "2026-08-21 13:30:00", "open": 2, "high": 2,
                    "low": 2, "close": 2, "volume": 2}],
        }
        return bars_by_role[role]

    monkeypatch.setattr(vc, "_fetch_history_window", fake_window)
    out = vc.fetch_vvtr_kline("k", "ma2701", "15m", limit=100, base_url="http://x")

    assert len(windows) == 3
    am_from, am_to = windows[1]
    pm_from, pm_to = windows[2]
    now = datetime.now(_BJ).replace(tzinfo=None)
    am_to_dt = datetime.strptime(am_to, "%Y-%m-%d %H:%M:%S")
    pm_from_dt = datetime.strptime(pm_from, "%Y-%m-%d %H:%M:%S")
    pm_to_dt = datetime.strptime(pm_to, "%Y-%m-%d %H:%M:%S")
    assert am_from.endswith("00:00:00")
    assert am_to_dt <= now  # 早段终点 = min(今日12:00, now)
    # 无缺口守卫：近段起点 ≤ 早段终点（2026-08-21 夜盘少 6 根 15m 的回归）
    assert pm_from_dt <= am_to_dt
    # 近段：起点取 min(回看跨度, 今日12:00)；to 在未来（+1h 向上取整）
    assert pm_from_dt <= min(
        now - timedelta(minutes=40),
        now.replace(hour=12, minute=0, second=0, microsecond=0),
    ) + timedelta(minutes=5)
    assert pm_to_dt > now
    # 边界 5 分钟对齐：中继缓存键 5 分钟内稳定（切分时卡顿优化）
    assert pm_from_dt.minute % 5 == 0 and pm_from_dt.second == 0
    assert pm_to_dt.minute % 5 == 0 and pm_to_dt.second == 0
    # 三段合并去重、时间升序
    assert [b["time"] for b in out] == [
        "2026-08-20 22:45:00", "2026-08-21 09:00:00", "2026-08-21 13:30:00"]


def test_vvtr_daily_fetch_single_window(monkeypatch):
    windows: list[tuple[str, str]] = []

    def fake_window(api_key, base, sym, interval, f, t, limit):
        windows.append((f, t))
        return [{"time": "2026-08-20", "open": 1, "high": 1,
                 "low": 1, "close": 1, "volume": 1}]

    monkeypatch.setattr(vc, "_fetch_history_window", fake_window)
    vc.fetch_vvtr_kline("k", "ma2701", "1d", limit=100, base_url="http://x")
    assert len(windows) == 1  # 日线仍单窗（日期格式）


# ========== 4. /kline/current 解析 + 恢复态治愈 ==========

def test_parse_current_response():
    payload = {
        "code": 200,
        "data": [
            [{"s": "MA2701", "o": 2738, "h": 2742, "l": 2731, "c": 2735,
              "v": 5727, "b": "2026-08-21 22:15:00", "e": "2026-08-21 22:30:00"}],
            [{"s": "RB2610", "o": 3057, "h": 3059, "l": 3054, "c": 3057,
              "v": 36529, "b": "2026-08-21 22:15", "e": "2026-08-21 22:30:00"}],
            [{"s": "BAD", "o": 0, "h": 0, "l": 0, "c": 0, "v": 0, "b": ""}],
        ],
    }
    out = vc.parse_current_response(payload)
    assert set(out) == {"ma2701", "rb2610"}
    assert out["ma2701"]["time"] == "2026-08-21 22:15:00"
    assert out["ma2701"]["open"] == 2738.0 and out["ma2701"]["volume"] == 5727
    # 16 位时间串补秒
    assert out["rb2610"]["time"] == "2026-08-21 22:15:00"


class _Cfg:
    api_key = "k"
    kline_enabled = True
    base_url = "http://x"


def test_heal_recovered_forming_replaces_matching(monkeypatch):
    """时间键匹配的恢复态桶 → 权威 OHLC 替换 + 基线重建 + 清除 recovered"""
    from types import SimpleNamespace
    from app.services import kline as ksvc

    monkeypatch.setattr(
        "app.connectors.vvtr.config.get_vvtr_config_sync",
        lambda: SimpleNamespace(api_key="k", kline_enabled=True, base_url="http://x"),
    )
    monkeypatch.setattr(
        "app.connectors.vvtr.http_client.fetch_vvtr_current_bars",
        lambda api_key, symbols, period, base_url="": {
            "ma2701": {"time": "2026-08-21 22:15:00", "open": 2738.0,
                       "high": 2742.0, "low": 2731.0, "close": 2735.0,
                       "volume": 5727, "settle": None, "open_interest": None},
            # rb2610 时间键不匹配（上游滞后一根）→ 应跳过
            "rb2610": {"time": "2026-08-21 22:00:00", "open": 3057.0,
                       "high": 3059.0, "low": 3054.0, "close": 3057.0,
                       "volume": 100, "settle": None, "open_interest": None},
        },
    )

    r = _NullRedis({
        "market:live:quote:ma2701": json.dumps({"volume": 50000}),
        "market:live:quote:rb2610": json.dumps({"volume": 40000}),
    })
    ksvc._realtime_state.clear()
    ksvc._realtime_state["ma2701"] = {"15m": {
        "bar": {"time": "2026-08-21 22:15:00", "open": 2735.0, "high": 2735.0,
                "low": 2735.0, "close": 2735.0, "volume": 12,
                "settle": None, "open_interest": None},
        "bar_time": "2026-08-21 22:15:00",
        "period_start_volume": 49988,
        "open_locked": True,
        "recovered": True,
    }}
    ksvc._realtime_state["rb2610"] = {"15m": {
        "bar": {"time": "2026-08-21 22:15:00", "open": 3055.0, "high": 3055.0,
                "low": 3055.0, "close": 3055.0, "volume": 5,
                "settle": None, "open_interest": None},
        "bar_time": "2026-08-21 22:15:00",
        "period_start_volume": 39995,
        "open_locked": True,
        "recovered": True,
    }}

    healed = asyncio.run(ksvc.heal_recovered_forming(r))
    assert healed == 1
    st = ksvc._realtime_state["ma2701"]["15m"]
    assert st["bar"]["high"] == 2742.0
    assert st["period_start_volume"] == 50000 - 5727
    assert "recovered" not in st  # 已权威化，恢复正常固化资格
    # 不匹配的桶保持恢复态（禁固化保护不丢）
    assert ksvc._realtime_state["rb2610"]["15m"].get("recovered") is True
    # Redis payload 已回写且不含 recovered
    payload = json.loads(r._store["kline:realtime:ma2701:15m"])
    assert payload["bar"]["high"] == 2742.0
    assert "recovered" not in payload
    ksvc._realtime_state.clear()


def test_heal_skips_without_quote_baseline(monkeypatch):
    """拿不到当日累计量（基线不可重建）→ 保守跳过，保持禁固化"""
    from types import SimpleNamespace
    from app.services import kline as ksvc

    monkeypatch.setattr(
        "app.connectors.vvtr.config.get_vvtr_config_sync",
        lambda: SimpleNamespace(api_key="k", kline_enabled=True, base_url="http://x"),
    )
    monkeypatch.setattr(
        "app.connectors.vvtr.http_client.fetch_vvtr_current_bars",
        lambda api_key, symbols, period, base_url="": {
            "ma2701": {"time": "2026-08-21 22:15:00", "open": 2738.0,
                       "high": 2742.0, "low": 2731.0, "close": 2735.0,
                       "volume": 5727, "settle": None, "open_interest": None},
        },
    )
    r = _NullRedis()  # 无 quote 键
    ksvc._realtime_state.clear()
    ksvc._realtime_state["ma2701"] = {"15m": {
        "bar": {"time": "2026-08-21 22:15:00", "open": 2735.0, "high": 2735.0,
                "low": 2735.0, "close": 2735.0, "volume": 12,
                "settle": None, "open_interest": None},
        "bar_time": "2026-08-21 22:15:00",
        "period_start_volume": 0,
        "open_locked": True,
        "recovered": True,
    }}
    assert asyncio.run(ksvc.heal_recovered_forming(r)) == 0
    assert ksvc._realtime_state["ma2701"]["15m"].get("recovered") is True
    ksvc._realtime_state.clear()
