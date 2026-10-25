"""K 线历史数据源健康监控测试 —— 探测三态 + 失败计数读取 + Redis 写入（无网络）"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import kline_source_health as ksh


class _FakeRedis:
    """最小 Redis 假实现（异步接口），支持 get/set/exists/scan_iter/pipeline"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def scan_iter(self, pattern: str):  # type: ignore[no-untyped-def]
        # 简化：只支持 * 通配（前缀匹配）
        prefix = pattern.rstrip("*")
        for key in list(self.store.keys()):
            if key.startswith(prefix):
                yield key

    def pipeline(self) -> "_FakePipe":
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self.r = r
        self._ops: list[tuple[str, tuple]] = []

    def set(self, key: str, value: str, ex: int | None = None) -> "_FakePipe":
        self._ops.append(("set", (key, value, ex)))
        return self

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, args in self._ops:
            if op == "set":
                key, value, _ex = args
                self.r.store[key] = value
                results.append(True)
        return results


def _bar(time: str = "2026-08-09", close: float = 3000.0) -> dict[str, Any]:
    return {
        "time": time,
        "open": close,
        "high": close + 10,
        "low": close - 10,
        "close": close,
        "volume": 100000,
        "settle": None,
        "open_interest": 200000,
    }


# ---------- 探测三态 ----------


@pytest.mark.asyncio
async def test_probe_green_on_valid_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """有效 bar + 快响应 → green"""
    r = _FakeRedis()
    monkeypatch.setattr(ksh, "_fetch_one", lambda src, sym, per: [_bar()])
    h = await ksh.probe_kline_source(r, "sina")
    assert h["status"] == "green"
    assert h["has_data"] is True
    assert h["last_bar_date"] == "2026-08-09"
    assert h["latency_ms"] is not None and h["latency_ms"] >= 0
    assert h["reason"] is None


@pytest.mark.asyncio
async def test_probe_red_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """空数据 → red"""
    r = _FakeRedis()
    monkeypatch.setattr(ksh, "_fetch_one", lambda src, sym, per: [])
    h = await ksh.probe_kline_source(r, "eastmoney")
    assert h["status"] == "red"
    assert h["has_data"] is False
    assert h["reason"] == "无数据"


@pytest.mark.asyncio
async def test_probe_red_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetcher 抛异常 → red"""
    r = _FakeRedis()

    def _boom(src: str, sym: str, per: str) -> list:
        raise RuntimeError("network down")

    monkeypatch.setattr(ksh, "_fetch_one", _boom)
    h = await ksh.probe_kline_source(r, "tqsdk")
    assert h["status"] == "red"
    assert "探测异常" in (h["reason"] or "")


@pytest.mark.asyncio
async def test_probe_reads_fails_and_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis 预置 fails 计数 + cooldown → 探测结果展示（但不影响 status 判定）"""
    from app.data import kline_provider as kp

    r = _FakeRedis()
    # 预置运行时失败计数 3 次 + 冷却标记
    r.store[kp._fails_key("sina")] = "3"
    r.store[kp._cooldown_key("sina")] = "1"
    monkeypatch.setattr(ksh, "_fetch_one", lambda src, sym, per: [_bar()])
    h = await ksh.probe_kline_source(r, "sina")
    # 探测本身成功 → green（fails/cooldown 只是展示，不改变探测 status）
    assert h["status"] == "green"
    assert h["fails"] == 3
    assert h["in_cooldown"] is True


@pytest.mark.asyncio
async def test_probe_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 Redis（None）：不读计数，仍能判定状态"""
    monkeypatch.setattr(ksh, "_fetch_one", lambda src, sym, per: [_bar()])
    h = await ksh.probe_kline_source(None, "sina")
    assert h["status"] == "green"
    assert h["fails"] == 0
    assert h["in_cooldown"] is False


# ---------- 多源收集 + Redis 写入 ----------


@pytest.mark.asyncio
async def test_collect_multiple_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """收集多源：每源独立探测"""
    r = _FakeRedis()
    results = {"sina": [_bar()], "tqsdk": [], "eastmoney": [_bar("2026-08-08")]}

    def _mock(src: str, sym: str, per: str) -> list:
        return results.get(src, [])

    monkeypatch.setattr(ksh, "_fetch_one", _mock)
    healths = await ksh.collect_kline_sources_health(r, ["sina", "tqsdk", "eastmoney"])
    assert healths["sina"]["status"] == "green"
    assert healths["tqsdk"]["status"] == "red"
    assert healths["eastmoney"]["status"] == "green"
    assert healths["eastmoney"]["last_bar_date"] == "2026-08-08"


@pytest.mark.asyncio
async def test_write_health_to_redis() -> None:
    """健康结果写 Redis：键格式 + JSON 可解析"""
    r = _FakeRedis()
    healths = {
        "sina": {"status": "green", "source": "sina", "latency_ms": 120},
        "eastmoney": {"status": "red", "source": "eastmoney", "reason": "无数据"},
    }
    await ksh.write_kline_source_health_to_redis(r, healths)
    assert ksh._health_key("sina") in r.store
    assert ksh._health_key("eastmoney") in r.store
    parsed = json.loads(r.store[ksh._health_key("sina")])
    assert parsed["status"] == "green"
    assert parsed["latency_ms"] == 120


@pytest.mark.asyncio
async def test_write_health_none_redis() -> None:
    """Redis 为 None → 不报错"""
    await ksh.write_kline_source_health_to_redis(None, {"sina": {}})
    # 无异常即通过


@pytest.mark.asyncio
async def test_collect_handles_per_source_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """某源探测抛异常 → 收集层兜底为 red，不影响其他源"""

    def _flaky(src: str, sym: str, per: str) -> list:
        if src == "tqsdk":
            raise RuntimeError("boom")
        return [_bar()]

    monkeypatch.setattr(ksh, "_fetch_one", _flaky)
    r = _FakeRedis()
    healths = await ksh.collect_kline_sources_health(r, ["sina", "tqsdk"])
    assert healths["sina"]["status"] == "green"
    assert healths["tqsdk"]["status"] == "red"


# ---------- 常量 ----------


def test_health_key_format() -> None:
    assert ksh._health_key("sina") == "kline:source:health:sina"
    assert ksh._health_key("eastmoney") == "kline:source:health:eastmoney"


def test_health_key_prefix_constant() -> None:
    """健康键前缀与 admin.py 端点 SCAN 使用的一致"""
    assert ksh.HEALTH_KEY_PREFIX == "kline:source:health"
