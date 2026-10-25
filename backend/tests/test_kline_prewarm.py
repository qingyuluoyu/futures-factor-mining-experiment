"""盘前预热测试：清单/节奏/幂等/容错/观测（openspec kline-prewarm）"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.services import kline_prewarm as pw

_BJ = timezone(timedelta(hours=8))
# 固定周一 09:00（避开真实星期几导致的抖动）
_MON_0900 = datetime(2026, 8, 24, 9, 0, tzinfo=_BJ)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.hstore: dict[str, dict[str, str]] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)

    async def hset(self, key: str, mapping: dict[str, str] | None = None):
        self.hstore.setdefault(key, {}).update(mapping or {})

    async def expire(self, key: str, seconds: int):
        return True

    async def hgetall(self, key: str):
        return dict(self.hstore.get(key, {}))


def _patch_common(monkeypatch, masters: dict, gkh=None, calls=None):
    async def fake_masters(r):
        return masters

    async def fake_gkh(redis, symbol, period, limit=0, **kw):
        if calls is not None:
            calls.append((symbol, period, limit))
        return []

    monkeypatch.setattr(
        "app.services.contract_master.get_master_contracts", fake_masters
    )
    monkeypatch.setattr(
        "app.services.kline.get_kline_history", gkh or fake_gkh
    )
    monkeypatch.setattr(pw, "ITEM_INTERVAL", 0.0)


def test_run_once_full_matrix(monkeypatch):
    calls: list[tuple] = []
    _patch_common(
        monkeypatch,
        {"rb": {"master": "rb2701"}, "ma": {"master": "ma2701"}},
        calls=calls,
    )
    r = _FakeRedis()
    done = asyncio.run(pw.run_prewarm_once(r))
    assert done == 2 * 6 == 12
    assert len(calls) == 12
    assert {c[0] for c in calls} == {"rb2701", "ma2701"}
    assert {c[1] for c in calls} == set(pw.PREWARM_PERIODS)
    assert all(c[2] == pw.LIMIT_PER_PERIOD for c in calls)
    status = asyncio.run(pw.prewarm_status(r))
    assert status["done"] == status["total"] == 12
    assert status["running"] is False
    assert status["finished_at"]
    # 状态发布到 Redis（backend 多 worker：端点可能打到非选主 worker）
    assert r.hstore.get(pw.STATUS_KEY, {}).get("total") == "12"


def test_status_prefers_redis_over_module(monkeypatch):
    """多 worker 语义：Redis 有值优先于本进程模块字典"""
    r = _FakeRedis()
    asyncio.run(
        pw._publish(r, running=True, done=5, total=396, last_error="", last_date="")
    )
    pw._status.update(done=0, total=0, running=False)
    status = asyncio.run(pw.prewarm_status(r))
    assert status["done"] == 5 and status["total"] == 396
    assert status["running"] is True
    # Redis 不可用/为空 → 回退模块字典
    fallback = asyncio.run(pw.prewarm_status(None))
    assert fallback["done"] == 0


def test_empty_master_list_skips(monkeypatch):
    calls: list[tuple] = []
    _patch_common(monkeypatch, {}, calls=calls)
    r = _FakeRedis()
    done = asyncio.run(pw.run_prewarm_once(r))
    assert done == 0
    assert calls == []
    assert "空" in asyncio.run(pw.prewarm_status(r))["last_error"]


def test_single_failure_does_not_abort(monkeypatch):
    calls: list[tuple] = []

    async def flaky_gkh(redis, symbol, period, limit=0, **kw):
        calls.append((symbol, period))
        if symbol == "ma2701" and period == "1d":
            raise RuntimeError("source down")
        return []

    _patch_common(
        monkeypatch,
        {"rb": {"master": "rb2701"}, "ma": {"master": "ma2701"}},
        gkh=flaky_gkh,
        calls=calls,
    )
    monkeypatch.setattr(pw, "FAIL_PAUSE_SECONDS", 0.0)
    done = asyncio.run(pw.run_prewarm_once(_FakeRedis()))
    assert done == 12  # 单项失败不中断
    assert len(calls) == 12


def test_maybe_prewarm_runs_once_per_day(monkeypatch):
    calls: list[tuple] = []
    _patch_common(monkeypatch, {"rb": {"master": "rb2701"}}, calls=calls)
    r = _FakeRedis()
    monkeypatch.setattr(pw, "_start_hhmm", lambda: (8, 40))

    asyncio.run(pw._maybe_prewarm(r, bj=_MON_0900))
    assert len(calls) == 6
    done_key = f"{pw.DONE_KEY_PREFIX}2026-08-24"
    assert r.store.get(done_key) == "1"

    # 同日第二次：SET NX 抢不到执行权，直接退出（幂等）
    asyncio.run(pw._maybe_prewarm(r, bj=_MON_0900))
    assert len(calls) == 6
    assert r.store.get(done_key) == "1"  # 完成态覆盖 running

    # 判重键存在时（新进程/其他 worker）：同样抢不到
    pw._status["last_date"] = ""
    asyncio.run(pw._maybe_prewarm(r, bj=_MON_0900))
    assert len(calls) == 6

    # 竞态语义：键值 running（他方在跑）时也跳过
    r.store[done_key] = "running"
    asyncio.run(pw._maybe_prewarm(r, bj=_MON_0900))
    assert len(calls) == 6


def test_maybe_prewarm_skips_before_start_time(monkeypatch):
    calls: list[tuple] = []
    _patch_common(monkeypatch, {"rb": {"master": "rb2701"}}, calls=calls)
    monkeypatch.setattr(pw, "_start_hhmm", lambda: (8, 40))
    r = _FakeRedis()
    # 周一 07:00，未到 08:40 → 不跑
    asyncio.run(pw._maybe_prewarm(r, bj=datetime(2026, 8, 24, 7, 0, tzinfo=_BJ)))
    assert calls == []
    # 周六 09:00 → 不跑
    asyncio.run(pw._maybe_prewarm(r, bj=datetime(2026, 8, 29, 9, 0, tzinfo=_BJ)))
    assert calls == []


def test_status_snapshot_is_copy():
    s1 = asyncio.run(pw.prewarm_status(None))
    s1["done"] = 999
    assert asyncio.run(pw.prewarm_status(None))["done"] != 999
