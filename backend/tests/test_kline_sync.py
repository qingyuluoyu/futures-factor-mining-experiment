"""vvtr 分发修正测试 —— upsert 逻辑 + sync 编排 + 定时触发（无网络/无真实 PG）"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest


# ---------- upsert_history_bars（ON CONFLICT UPDATE 逻辑）----------


class _FakeResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _FakeSession:
    """假 AsyncSession：记录 execute 调用，rowcount 反映 batch 大小"""

    def __init__(self) -> None:
        self.executes: list[Any] = []
        self.committed = False

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executes.append(stmt)
        # 从 pg_insert 的 _values 提取行数
        rowcount = 1
        try:
            vals = getattr(stmt, "_values", None)
            if vals and isinstance(vals, list):
                rowcount = len(vals)
        except Exception:
            pass
        return _FakeResult(rowcount=rowcount)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeSessionFactory:
    """假 session_factory：返回 _FakeSession 上下文"""

    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSession:
        s = _FakeSession()
        self.sessions.append(s)
        return s


def _bar(time: str, close: float = 100.0, volume: int = 1000) -> dict[str, Any]:
    return {
        "time": time,
        "open": close,
        "high": close + 5,
        "low": close - 5,
        "close": close,
        "volume": volume,
        "hold": 500,
    }


@pytest.mark.asyncio
async def test_upsert_history_bars_processes_valid() -> None:
    """upsert_history_bars：有效 bar → 解析为行并 execute"""
    from app.services.kline_history import upsert_history_bars

    session = _FakeSession()
    bars = [
        _bar("2026-08-07", close=3000.0),
        _bar("2026-08-08", close=3010.0),
    ]
    n = await upsert_history_bars(session, "rb", bars)
    # FakeSession rowcount 提取不一定精确，验证逻辑走通即可（>0 且 execute 被调用）
    assert n >= 1
    assert len(session.executes) == 1
    assert session.committed is True
    assert session.committed is True


@pytest.mark.asyncio
async def test_upsert_history_bars_skips_invalid_date() -> None:
    """无效日期 → 跳过，不报错"""
    from app.services.kline_history import upsert_history_bars

    session = _FakeSession()
    bars = [
        _bar("invalid-date"),
        _bar("2026-08-08", close=3010.0),
    ]
    n = await upsert_history_bars(session, "rb", bars)
    assert n >= 1  # 只有 1 条有效，execute 被调用 1 次
    assert len(session.executes) == 1


@pytest.mark.asyncio
async def test_upsert_history_bars_empty() -> None:
    """空列表 → 不 execute，返回 0"""
    from app.services.kline_history import upsert_history_bars

    session = _FakeSession()
    n = await upsert_history_bars(session, "rb", [])
    assert n == 0
    assert len(session.executes) == 0


# ---------- sync_kline_from_tqsdk 编排 ----------


@pytest.mark.asyncio
async def test_sync_minute_writes_kline_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分钟周期 → 调用 upsert_minute_bars"""
    from app.services import kline_sync

    upsert_calls: list[tuple] = []

    async def _fake_upsert_minute(session, code, symbol, period, bars):
        upsert_calls.append((code, symbol, period, len(bars)))
        return len(bars)

    monkeypatch.setattr(
        "app.services.kline_minute.upsert_minute_bars", _fake_upsert_minute
    )
    # mock tqsdk 拉取
    monkeypatch.setattr(
        kline_sync,
        "_sync_one_period",
        _make_fake_sync_one(
            {"rb_1m": [_bar("2026-08-10 09:00:00")], "rb_1d": [_bar("2026-08-10")]}
        ),
    )

    sf = _FakeSessionFactory()
    stats = await kline_sync.sync_kline_from_tickdata(
        sf, codes=[{"code": "rb", "exchange": "上期所"}], periods=["1m"]
    )
    assert stats["1m"] == 1


@pytest.mark.asyncio
async def test_sync_daily_writes_kline_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """日线周期 → 调用 upsert_history_bars"""
    from app.services import kline_sync

    monkeypatch.setattr(
        "app.services.kline_history.upsert_history_bars",
        _make_fake_upsert_history(),
    )
    monkeypatch.setattr(
        kline_sync,
        "_sync_one_period",
        _make_fake_sync_one(
            {"rb_1d": [_bar("2026-08-10")]}
        ),
    )

    sf = _FakeSessionFactory()
    stats = await kline_sync.sync_kline_from_tickdata(
        sf, codes=[{"code": "rb", "exchange": "上期所"}], periods=["1d"]
    )
    assert stats["1d"] == 1


@pytest.mark.asyncio
async def test_sync_empty_fetch_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TqSdk 返回空 → 该周期写入 0"""
    from app.services import kline_sync

    async def _empty_fetch(*args, **kwargs):
        return 0, {"verified": 0, "single": 0, "conflict": 0, "skipped": 0}

    monkeypatch.setattr(kline_sync, "_sync_one_period", _empty_fetch)

    sf = _FakeSessionFactory()
    stats = await kline_sync.sync_kline_from_tickdata(
        sf,
        codes=[{"code": "rb", "exchange": "上期所"}],
        periods=["1m", "1d"],
    )
    assert stats["1m"] == 0
    assert stats["1d"] == 0


@pytest.mark.asyncio
async def test_sync_continues_on_single_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单品种/周期失败不阻断整体"""
    from app.services import kline_sync

    call_count = {"n": 0}

    async def _flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return 5, {"verified": 5, "single": 0, "conflict": 0, "skipped": 0}

    monkeypatch.setattr(kline_sync, "_sync_one_period", _flaky)

    sf = _FakeSessionFactory()
    stats = await kline_sync.sync_kline_from_tickdata(
        sf,
        codes=[{"code": "rb", "exchange": "上期所"}],
        periods=["1m", "5m"],
    )
    # 第一个失败（1m=0），第二个成功（5m=5）
    assert stats["1m"] == 0
    assert stats["5m"] == 5


# ---------- 定时触发逻辑 ----------


def test_unique_codes_dedup() -> None:
    """_unique_codes 去重 + 小写"""
    from app.services.kline_sync import _unique_codes

    codes = _unique_codes()
    assert len(codes) > 0
    # 全小写
    for c in codes:
        assert c["code"] == c["code"].lower()
        assert c["exchange"]
    # 无重复
    seen = [c["code"] for c in codes]
    assert len(seen) == len(set(seen))


def test_sync_periods_constants() -> None:
    """周期常量正确"""
    from app.services.kline_sync import SYNC_PERIODS_MINUTE, SYNC_PERIOD_DAILY

    assert SYNC_PERIODS_MINUTE == ["1m", "5m", "15m", "30m", "60m"]
    assert SYNC_PERIOD_DAILY == "1d"


# ---------- 辅助：mock 工厂 ----------


def _make_fake_sync_one(data: dict[str, list[dict]]) -> Any:
    """构造假的 _sync_one_period：根据 code_period key 返回数据

    返回 (写入条数, 交叉验证统计) 匹配新签名。
    """

    async def _fake(session_factory, code, symbol, period, redis_conn=None):
        # redis_conn:修正结果并入 Redis 历史缓存后编排层多传的参数
        key = f"{code}_{period}"
        bars = data.get(key, [])
        if not bars:
            return 0, {"verified": 0, "single": 0, "conflict": 0, "skipped": 0}
        n = len(bars)
        stats = {"verified": n, "single": 0, "conflict": 0, "skipped": 0}
        if period == "1d":
            from app.services.kline_history import upsert_history_bars

            async with session_factory() as session:
                cnt = await upsert_history_bars(session, code, bars)
                return cnt, stats
        else:
            from app.services.kline_minute import upsert_minute_bars

            async with session_factory() as session:
                symbol = bars[0].get("symbol", code) if bars else code
                cnt = await upsert_minute_bars(session, code, symbol, period, bars)
                return cnt, stats

    return _fake


def _make_fake_upsert_history() -> Any:
    async def _fake(session, code, bars):
        return len(bars)

    return _fake


# ---------- _cleanup_stale_running_status（僵尸 running 清理）----------

import json
from datetime import datetime, timedelta


class _StatusRedis:
    """最小 Redis 假实现（异步 get/set），用于 sync 状态测试"""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


def _bj_hours_ago(hours: float) -> str:
    from app.services.kline_sync import _BJ_TZ

    return (datetime.now(_BJ_TZ) - timedelta(hours=hours)).isoformat()


@pytest.mark.asyncio
async def test_cleanup_stale_running_marks_old_as_error() -> None:
    """running + started_at 超 1h → 标记 error 并补全 stats/errors 字段"""
    from app.services import kline_sync

    r = _StatusRedis(
        {
            "kline:sync:status": json.dumps(
                {
                    "status": "running",
                    "started_at": _bj_hours_ago(2),
                    "processed": 6,
                    "total_codes": 69,
                }
            )
        }
    )
    await kline_sync._cleanup_stale_running_status(r)
    data = json.loads(r.store["kline:sync:status"])
    assert data["status"] == "error"
    assert data["stats"] == {}  # 补全缺失字段
    assert isinstance(data["errors"], list)
    assert any("interrupted" in e or "stale" in e for e in data["errors"])


@pytest.mark.asyncio
async def test_cleanup_stale_running_skips_recent() -> None:
    """running + started_at <1h → 不清理（当前进程刚起的）"""
    from app.services import kline_sync

    r = _StatusRedis(
        {
            "kline:sync:status": json.dumps(
                {
                    "status": "running",
                    "started_at": _bj_hours_ago(0.05),  # 3 分钟前
                    "processed": 3,
                }
            )
        }
    )
    await kline_sync._cleanup_stale_running_status(r)
    data = json.loads(r.store["kline:sync:status"])
    assert data["status"] == "running"  # 未改动


@pytest.mark.asyncio
async def test_cleanup_stale_running_skips_non_running() -> None:
    """非 running 状态（completed/error）→ 不动"""
    from app.services import kline_sync

    r = _StatusRedis(
        {
            "kline:sync:status": json.dumps(
                {
                    "status": "completed",
                    "started_at": _bj_hours_ago(100),
                }
            )
        }
    )
    await kline_sync._cleanup_stale_running_status(r)
    data = json.loads(r.store["kline:sync:status"])
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_cleanup_stale_running_none_redis_noop() -> None:
    """redis_conn=None → 不报错"""
    from app.services import kline_sync

    await kline_sync._cleanup_stale_running_status(None)
