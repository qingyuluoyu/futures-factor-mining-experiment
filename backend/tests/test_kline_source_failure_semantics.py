"""源失败计数语义测试（openspec kline-source-failure-semantics）

2026-08-25 ma2609 事件根因：空结果=合约无数据被当成源故障，
3 次即把唯一源打进 60s 冷却=全断。新语义：只有异常/过期计失败。
"""

from __future__ import annotations

import pytest

from app.data import kline_provider as kp


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for k in keys:
            self.store.pop(k, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def incr(self, key: str) -> int:
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> None:
        pass

    def pipeline(self) -> "_FakePipe":
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self.r = r
        self._ops: list[tuple[str, tuple]] = []

    def incr(self, key: str) -> "_FakePipe":
        self._ops.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int) -> "_FakePipe":
        self._ops.append(("expire", (key, seconds)))
        return self

    def delete(self, key: str) -> "_FakePipe":
        self._ops.append(("delete", (key,)))
        return self

    async def execute(self):
        out = []
        for op, args in self._ops:
            fn = getattr(self.r, op)
            out.append(await fn(*args) if op != "expire" else None)
        return out


def _bar(t: str) -> dict:
    return {"time": t, "open": 3000, "high": 3010, "low": 2990, "close": 3005, "volume": 10}


def _install(monkeypatch, primary_result, backup_result=None, calls=None):
    """primary_result: list=返回值 / None=抛异常"""

    def make(result):
        def fetch(symbol: str, period: str):
            if calls is not None:
                calls.append(1)
            if result is None:
                raise RuntimeError("source down")
            return list(result)

        return fetch

    monkeypatch.setattr(kp, "_register_fetchers", lambda *a, **k: None)
    monkeypatch.setattr(
        kp,
        "_SOURCE_FETCHERS",
        {
            "sina": make(primary_result),
            "simnow": make(backup_result if backup_result is not None else []),
            "remote": make([]),
            "vvtr": make([]),
        },
    )


@pytest.mark.asyncio
async def test_dead_symbols_do_not_poison_source(monkeypatch):
    """3 个死合约（空结果）连续请求 → 源失败计数不增长、不进冷却"""
    r = _FakeRedis()
    _install(monkeypatch, [])
    for _ in range(kp.FAIL_THRESHOLD):
        bars = await kp.fetch_kline_with_fallback(
            r, "wh2703", "1m", primary="sina", backups=["simnow"]
        )
        assert bars == []
    assert kp._fails_key("sina") not in r.store
    assert await r.exists(kp._cooldown_key("sina")) == 0


@pytest.mark.asyncio
async def test_exceptions_still_trigger_cooldown(monkeypatch):
    """拉取异常 3 次 → 照常进冷却（真故障语义保留）"""
    r = _FakeRedis()
    _install(monkeypatch, None)
    for _ in range(kp.FAIL_THRESHOLD):
        await kp.fetch_kline_with_fallback(
            r, "rb2610", "1d", primary="sina", backups=["simnow"]
        )
    assert await r.exists(kp._cooldown_key("sina")) == 1


@pytest.mark.asyncio
async def test_empty_still_falls_through_to_backup(monkeypatch):
    """空结果仍立即降级下一源（行为不变）"""
    r = _FakeRedis()
    calls: list[int] = []

    def make(result):
        def fetch(symbol: str, period: str):
            calls.append(1)
            return list(result)

        return fetch

    monkeypatch.setattr(kp, "_register_fetchers", lambda *a, **k: None)
    monkeypatch.setattr(
        kp,
        "_SOURCE_FETCHERS",
        {"sina": make([]), "simnow": make([_bar("2026-08-07 10:00:00")])},
    )
    bars = await kp.fetch_kline_with_fallback(
        r, "ri2707", "15m", primary="sina", backups=["simnow"]
    )
    assert len(calls) == 2  # 主源空后立即打了备用
    assert len(bars) == 1
