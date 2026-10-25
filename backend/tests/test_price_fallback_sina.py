"""最新参考价兜底链回归 —— Redis 重建 / 休市报价缺失场景

2026-09-08 事故：部署重建 Redis 时 rb2701 夜盘已收盘，market:live:quote 键
无法回填，resolve_last_price 落到 mock 默认 5000（rb2701 不在
MOCK_BASE_PRICES），任务浮盈虚增至 +18540（(5000-3146)×10×1手）。
修复口径：统一报价 > sina 源快照（真实价，收盘后常驻）> 调用方 fallback >
（仅 allow_mock=True 时）mock 基准价；展示 / 交易定价 / 价格带一律禁 mock。
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.services.paper.price_band import resolve_last_price
from app.services.ai_trading.task.position_pnl import get_task_position_pnl

UNIFIED_KEY = "market:live:quote:rb2701"
SINA_KEY = "market:live:source:sina:quote:rb2701"
LEGACY_KEY = "market:quote:rb2701"


class _FakeRedis:
    def __init__(self, store: dict[str, str]) -> None:
        self._store = store
        self.get = AsyncMock(side_effect=self._get)

    async def _get(self, key: str):
        return self._store.get(key)


@pytest.mark.asyncio
async def test_unified_quote_takes_priority() -> None:
    redis = _FakeRedis(
        {UNIFIED_KEY: json.dumps({"last_price": 3160.0}), SINA_KEY: json.dumps({"last_price": 3170.0})}
    )
    out = await resolve_last_price("rb2701", redis, None)
    assert out == Decimal("3160.0")


@pytest.mark.asyncio
async def test_sina_snapshot_fallback_when_unified_missing() -> None:
    """事故场景：统一报价缺失（Redis 重建+休市）→ 用 sina 源快照真实价"""
    redis = _FakeRedis({SINA_KEY: json.dumps({"last_price": 3170.0})})
    out = await resolve_last_price("rb2701", redis, None)
    assert out == Decimal("3170.0")


@pytest.mark.asyncio
async def test_display_paths_never_fall_to_mock() -> None:
    """allow_mock=False：无任何报价时返回 None，绝不返回 mock 默认 5000"""
    redis = _FakeRedis({})
    assert await resolve_last_price("rb2701", redis, None, allow_mock=False) is None
    # fallback 参数仍生效（在 mock 之前）
    assert await resolve_last_price("rb2701", redis, 3146.0, allow_mock=False) == Decimal("3146.0")


@pytest.mark.asyncio
async def test_mock_still_available_when_allowed() -> None:
    """兼容旧行为：显式 allow_mock=True（默认）且无报价时仍回落 mock 基准价"""
    redis = _FakeRedis({})
    out = await resolve_last_price("rb2701", redis, None)
    assert out == Decimal("5000")  # rb2701 未定义 → 默认 5000


@pytest.mark.asyncio
async def test_bad_snapshot_json_ignored() -> None:
    redis = _FakeRedis({UNIFIED_KEY: "not-json", SINA_KEY: json.dumps({"last_price": 0})})
    assert await resolve_last_price("rb2701", redis, None, allow_mock=False) is None


def _pos_session(avg: Decimal = Decimal("3146")) -> AsyncMock:
    session = AsyncMock()
    pos = SimpleNamespace(
        quantity=1,
        avg_price=avg,
        multiplier=10,
        direction="long",
    )
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: pos)
    )
    return session


@pytest.mark.asyncio
async def test_position_pnl_uses_sina_price_not_mock() -> None:
    """事故复现：无统一报价 + sina 快照 3170 → 浮盈 (3170-3146)×10 = +240，
    而不是 mock 5000 的 +18540"""
    redis = _FakeRedis({SINA_KEY: json.dumps({"last_price": 3170.0})})
    pnl = await get_task_position_pnl(_pos_session(), UUID(int=1), "rb2701", redis, task_id=UUID(int=2))
    assert pnl["last_price"] == 3170.0
    assert pnl["unrealized_pnl"] == 240.0


@pytest.mark.asyncio
async def test_position_pnl_no_quote_falls_to_avg_zero_pnl() -> None:
    """完全无报价：回落持仓均价，浮盈 0（而不是 mock 虚增）"""
    redis = _FakeRedis({})
    pnl = await get_task_position_pnl(_pos_session(), UUID(int=1), "rb2701", redis, task_id=UUID(int=2))
    assert pnl["last_price"] == 3146.0
    assert pnl["unrealized_pnl"] == 0.0
