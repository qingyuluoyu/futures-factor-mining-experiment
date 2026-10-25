"""实时 K线 bar → PG 持久化测试 —— 节流/空值/session_factory 注入（无真实 PG）"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import kline_persist as kp


class _FakeResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self) -> None:
        self.executes: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executes.append(stmt)
        return _FakeResult(rowcount=1)

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeSessionFactory:
    def __init__(self) -> None:
        self.session = _FakeSession()

    def __call__(self) -> _FakeSession:
        return self.session


def _bar(time: str = "2026-08-10 10:30:00", close: float = 3000.0) -> dict[str, Any]:
    return {
        "time": time,
        "open": close,
        "high": close + 5,
        "low": close - 5,
        "close": close,
        "volume": 500,
        "open_interest": 200000,
    }


@pytest.fixture(autouse=True)
def _reset_persist_state(monkeypatch: pytest.MonkeyPatch):
    """每个测试前重置 kline_persist 的全局状态"""
    kp._written.clear()
    kp._session_factory = None
    yield
    kp._written.clear()
    kp._session_factory = None


@pytest.mark.asyncio
async def test_persist_skipped_without_session_factory() -> None:
    """session_factory 未注入 → 返回 0，不报错"""
    n = await kp.persist_closed_bar("rb2610", _bar())
    assert n == 0


@pytest.mark.asyncio
async def test_persist_skipped_on_empty_bar() -> None:
    """空 bar / 无 time → 返回 0"""
    kp.set_session_factory(_FakeSessionFactory())
    assert await kp.persist_closed_bar("rb2610", {}) == 0
    assert await kp.persist_closed_bar("rb2610", {"time": ""}) == 0
    assert await kp.persist_closed_bar("rb2610", None) == 0  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_persist_writes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一根 bar 只写一次（节流）"""
    sf = _FakeSessionFactory()
    kp.set_session_factory(sf)

    # mock upsert_minute_bars
    call_count = {"n": 0}

    async def _fake_upsert(session, code, symbol, period, bars):
        call_count["n"] += 1
        return len(bars)

    monkeypatch.setattr(
        "app.services.kline_minute.upsert_minute_bars", _fake_upsert
    )

    bar = _bar("2026-08-10 10:30:00")
    n1 = await kp.persist_closed_bar("rb2610", bar)
    n2 = await kp.persist_closed_bar("rb2610", bar)  # 重复 → 跳过
    n3 = await kp.persist_closed_bar("rb2610", bar)  # 再重复 → 跳过

    assert call_count["n"] == 1  # 只调用了一次 upsert
    assert n1 == 1
    assert n2 == 0
    assert n3 == 0


@pytest.mark.asyncio
async def test_persist_different_bars_each_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同 bar_time 的 bar 各写一次"""
    sf = _FakeSessionFactory()
    kp.set_session_factory(sf)

    written_bars: list[str] = []

    async def _fake_upsert(session, code, symbol, period, bars):
        written_bars.append(bars[0]["time"])
        return 1

    monkeypatch.setattr(
        "app.services.kline_minute.upsert_minute_bars", _fake_upsert
    )

    await kp.persist_closed_bar("rb2610", _bar("2026-08-10 10:30:00"))
    await kp.persist_closed_bar("rb2610", _bar("2026-08-10 10:31:00"))
    await kp.persist_closed_bar("rb2610", _bar("2026-08-10 10:32:00"))

    assert len(written_bars) == 3
    assert "2026-08-10 10:30:00" in written_bars
    assert "2026-08-10 10:32:00" in written_bars


@pytest.mark.asyncio
async def test_persist_different_symbols_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同 symbol 同一 bar_time 各自独立写"""
    sf = _FakeSessionFactory()
    kp.set_session_factory(sf)

    call_count = {"n": 0}

    async def _fake_upsert(session, code, symbol, period, bars):
        call_count["n"] += 1
        return 1

    monkeypatch.setattr(
        "app.services.kline_minute.upsert_minute_bars", _fake_upsert
    )

    bar = _bar("2026-08-10 10:30:00")
    await kp.persist_closed_bar("rb2610", bar)
    await kp.persist_closed_bar("au2612", bar)  # 不同 symbol，同一时间

    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_persist_swallows_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upsert 抛异常 → 不传播，返回 0"""
    sf = _FakeSessionFactory()
    kp.set_session_factory(sf)

    async def _boom(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(
        "app.services.kline_minute.upsert_minute_bars", _boom
    )

    n = await kp.persist_closed_bar("rb2610", _bar())
    assert n == 0  # 异常被吞，不传播


@pytest.mark.asyncio
async def test_persist_unknown_symbol_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未知 symbol（get_code_for_symbol 返回空）→ 跳过"""
    sf = _FakeSessionFactory()
    kp.set_session_factory(sf)

    monkeypatch.setattr(
        "app.data.contracts.get_code_for_symbol", lambda s: ""
    )

    n = await kp.persist_closed_bar("xxunknown", _bar())
    assert n == 0


def test_set_session_factory() -> None:
    """set_session_factory 注入成功"""
    sf = _FakeSessionFactory()
    kp.set_session_factory(sf)
    assert kp._session_factory is sf


def test_trim_written_cache() -> None:
    """_written 超过阈值时清空"""
    # 填入超过阈值
    for i in range(kp._MAX_WRITTEN_CACHE + 100):
        kp._written.add((f"sym{i}", f"2026-08-10 10:{i:04d}:00"))
    assert len(kp._written) > kp._MAX_WRITTEN_CACHE
    kp._trim_written_cache()
    assert len(kp._written) == 0
