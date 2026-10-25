"""盘口动能检测 + 节假日休市自动暂停单测

时段表无节假日日历：法定节假日落在工作日会被误判开盘，任务照跑。
修复口径（用户定义）：只要盘口值（价/量/买卖档）长时间无变化，即视为休市。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.services.market_motion import (
    DEFAULT_STALE_SECONDS,
    is_symbol_motion_stale,
)

UID = UUID("55555555-5555-5555-5555-555555555555")


class FakeRedis:
    def __init__(
        self,
        quotes: dict[str, str] | None = None,
        motion: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.quotes = quotes or {}
        self.motion = motion or {}

    async def get(self, key: str):
        return self.quotes.get(key)

    async def hgetall(self, key: str):
        return dict(self.motion.get(key) or {})

    async def hset(self, key: str, mapping: dict | None = None):
        self.motion.setdefault(key, {}).update(mapping or {})


def _quote(price: float = 5000.0, volume: int = 1000, recv_ts: float | None = None):
    return json.dumps({
        "symbol": "v2701",
        "last_price": price,
        "volume": volume,
        "bid_price1": price - 1,
        "ask_price1": price + 1,
        "bid_volume1": 50,
        "ask_volume1": 60,
        "open_interest": 200000,
        "recv_ts": recv_ts if recv_ts is not None else time.time(),
    })


def _fp(price: float = 5000.0, volume: int = 1000) -> str:
    return (
        f"last_price={price}|volume={volume}|bid_price1={price - 1}"
        f"|ask_price1={price + 1}|bid_volume1=50|ask_volume1=60"
        f"|open_interest=200000"
    )


QKEY = "market:live:quote:v2701"
MKEY = "market:motion:live:v2701"


@pytest.mark.asyncio
async def test_first_observation_grace() -> None:
    """首次观测：记录指纹并给完整宽限期，不判休市"""
    r = FakeRedis(quotes={QKEY: _quote()})
    now = time.time()
    assert await is_symbol_motion_stale(r, "v2701", now_ts=now) is False
    assert r.motion[MKEY]["ts"] == now


@pytest.mark.asyncio
async def test_frozen_values_stale_even_with_fresh_recv_ts() -> None:
    """节假日核心场景：recv_ts 是新的（源在推）但值冻结 → 仍判休市"""
    now = time.time()
    r = FakeRedis(
        quotes={QKEY: _quote(recv_ts=now)},  # 快照新鲜
        motion={MKEY: {"fp": _fp(), "ts": now - DEFAULT_STALE_SECONDS - 60}},
    )
    assert await is_symbol_motion_stale(r, "v2701", now_ts=now) is True


@pytest.mark.asyncio
async def test_values_changed_not_stale() -> None:
    """值变化：不判休市且刷新时刻"""
    now = time.time()
    r = FakeRedis(
        quotes={QKEY: _quote(price=5001.0)},  # 价格变了
        motion={MKEY: {"fp": _fp(), "ts": now - 99999}},
    )
    assert await is_symbol_motion_stale(r, "v2701", now_ts=now) is False
    assert r.motion[MKEY]["ts"] == now


@pytest.mark.asyncio
async def test_quote_missing_stale_after_threshold() -> None:
    """行情整体缺失（源不推）：超过阈值同样判休市"""
    now = time.time()
    r = FakeRedis(motion={MKEY: {"fp": _fp(), "ts": now - 7200}})
    assert await is_symbol_motion_stale(r, "v2701", now_ts=now) is True


@pytest.mark.asyncio
async def test_within_grace_not_stale() -> None:
    """阈值内未变化：不判休市"""
    now = time.time()
    r = FakeRedis(
        quotes={QKEY: _quote()},
        motion={MKEY: {"fp": _fp(), "ts": now - 60}},
    )
    assert await is_symbol_motion_stale(r, "v2701", now_ts=now) is False


@pytest.mark.asyncio
async def test_redis_none_never_stale() -> None:
    """Redis 不可用：回落时段表行为"""
    assert await is_symbol_motion_stale(None, "v2701") is False


# ---------- session_auto 集成：节假日自动暂停/恢复 ----------

class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class _ExecResult:
    def __init__(self, items):
        self._scalars = _Scalars(items)

    def scalars(self):
        return self._scalars


class _SyncSession:
    """按任务状态过滤的极简 FakeSession"""

    def __init__(self, tasks):
        self._tasks = tasks

    async def execute(self, stmt):
        entities = stmt.column_descriptions
        entity = entities[0]["entity"] if entities else None
        from app.models.ai_trading.task import AITradingTask

        if entity is AITradingTask:
            status_val = None
            wc = stmt.whereclause
            # 单条件 where 无 .clauses(整体即 BinaryExpression),多条件才有
            for clause in getattr(wc, "clauses", [wc] if wc is not None else []):
                left = getattr(clause, "left", None)
                right = getattr(clause, "right", None)
                if getattr(left, "key", None) == "status":
                    status_val = getattr(right, "value", None)
            if status_val:
                return _ExecResult([t for t in self._tasks if t.status == status_val])
        return _ExecResult([])

    async def flush(self):
        pass


def _task(status: str = "running", pause_reason: str | None = None):
    return SimpleNamespace(
        id=UID, symbol="v2701", trading_mode="live",
        status=status, pause_reason=pause_reason,
        close_rules={}, timeframe="15m",
        paused_at=None, updated_at=None, next_bar_ts=None, note=None,
        run_started_at=None,
    )


@pytest.mark.asyncio
async def test_sync_pauses_on_frozen_market(monkeypatch) -> None:
    """时段表开盘 + 盘口冻结 → 暂停并注明节假日"""
    from app.services.ai_trading import session_auto

    monkeypatch.setattr(
        session_auto, "get_session_status", lambda sym: {"is_open": True}
    )
    monkeypatch.setattr(session_auto, "is_trading_hours", lambda: True)
    now = time.time()
    r = FakeRedis(
        quotes={QKEY: _quote()},
        motion={MKEY: {"fp": _fp(), "ts": now - 3600}},
    )
    task = _task()
    out = await session_auto.sync_tasks_with_session(_SyncSession([task]), r)
    assert out["paused"] == 1
    assert task.status == "paused"
    assert task.pause_reason == "market_closed"
    assert "节假日" in (task.note or "")


@pytest.mark.asyncio
async def test_sync_resumes_when_market_moving(monkeypatch) -> None:
    """盘口恢复变化 → market_closed 任务自动恢复"""
    from app.services.ai_trading import session_auto

    monkeypatch.setattr(
        session_auto, "get_session_status", lambda sym: {"is_open": True}
    )
    monkeypatch.setattr(session_auto, "is_trading_hours", lambda: True)
    now = time.time()
    # 盘口在动：价格已变，且 motion 时刻新
    r = FakeRedis(
        quotes={QKEY: _quote(price=5005.0)},
        motion={MKEY: {"fp": _fp(price=5004.0), "ts": now - 30}},
    )
    task = _task(status="paused", pause_reason="market_closed")
    out = await session_auto.sync_tasks_with_session(_SyncSession([task]), r)
    assert out["resumed"] == 1
    assert task.status == "running"


@pytest.mark.asyncio
async def test_sync_blocks_resume_while_frozen(monkeypatch) -> None:
    """节假日盘中：market_closed 任务不被时段表误恢复"""
    from app.services.ai_trading import session_auto

    monkeypatch.setattr(
        session_auto, "get_session_status", lambda sym: {"is_open": True}
    )
    monkeypatch.setattr(session_auto, "is_trading_hours", lambda: True)
    now = time.time()
    r = FakeRedis(
        quotes={QKEY: _quote()},
        motion={MKEY: {"fp": _fp(), "ts": now - 3600}},
    )
    task = _task(status="paused", pause_reason="market_closed")
    out = await session_auto.sync_tasks_with_session(_SyncSession([task]), r)
    assert out["resumed"] == 0
    assert task.status == "paused"
