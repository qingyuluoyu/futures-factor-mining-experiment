"""大单检测单测 —— 显著放量判定与去重（用 fake redis 记录发布）"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.notify.big_order_detect import (
    detect_and_publish,
    get_baseline,
    reset_detect_state,
)


class _FakeRedis:
    """最小化的 Redis 桩：记录 publish 调用"""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, channel: str, message: str) -> int:
        import json

        self.published.append((channel, json.loads(message)))
        return 1


def _orderbook(
    symbol: str,
    current_volume: int | None,
    direction: str = "",
    total_volume: int | None = None,
    avg_price: float = 3000.0,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "last_direction": direction,
        "asof": 1234567890,
        "stats": {
            "current_volume": {"value": current_volume, "source": "derived", "quality": "derived"},
            "volume": {"value": total_volume, "source": "direct", "quality": "direct"},
            "average_price": {"value": avg_price, "source": "derived", "quality": "derived"},
        },
    }


@pytest.fixture(autouse=True)
def _reset():
    reset_detect_state()
    yield
    reset_detect_state()


@pytest.mark.asyncio
async def test_first_seen_records_but_no_publish() -> None:
    """首次见到 symbol 记录总量但不推送（避免冷启动误报）"""
    r = _FakeRedis()
    n = await detect_and_publish(r, "live", _orderbook("rb2610", 100, "buy", total_volume=1000))
    assert n == 0
    assert r.published == []


@pytest.mark.asyncio
async def test_below_baseline_no_publish() -> None:
    """增量低于基线不推送"""
    r = _FakeRedis()
    await detect_and_publish(r, "live", _orderbook("rb2610", 10, "buy", total_volume=1000))
    n = await detect_and_publish(r, "live", _orderbook("rb2610", 30, "buy", total_volume=1030))
    assert n == 0


@pytest.mark.asyncio
async def test_no_direction_no_publish() -> None:
    """无方向（非 buy/sell）不推送"""
    r = _FakeRedis()
    await detect_and_publish(r, "live", _orderbook("rb2610", 10, "buy", total_volume=1000))
    n = await detect_and_publish(r, "live", _orderbook("rb2610", 200, "", total_volume=1200))
    assert n == 0


@pytest.mark.asyncio
async def test_publishes_on_significant_volume() -> None:
    """达到基线且总量增长 → 推送"""
    r = _FakeRedis()
    await detect_and_publish(r, "live", _orderbook("rb2610", 10, "buy", total_volume=1000))
    n = await detect_and_publish(r, "live", _orderbook("rb2610", 100, "sell", total_volume=1100))
    assert n == 1
    assert len(r.published) == 1
    channel, payload = r.published[0]
    assert channel == "ws:big-order:live"
    assert payload["type"] == "big_order_tick"
    assert payload["data"]["symbol"] == "rb2610"
    assert payload["data"]["direction"] == "sell"
    assert payload["data"]["volume"] == 100
    assert payload["data"]["price"] == 3000.0


@pytest.mark.asyncio
async def test_no_publish_when_total_not_growing() -> None:
    """总量未增长（同一增量）不重复推送"""
    r = _FakeRedis()
    await detect_and_publish(r, "live", _orderbook("rb2610", 10, "buy", total_volume=1000))
    await detect_and_publish(r, "live", _orderbook("rb2610", 100, "buy", total_volume=1100))
    assert len(r.published) == 1
    # 同一总量再来一次，current_volume 仍是 100（陈旧）→ 不推
    n = await detect_and_publish(r, "live", _orderbook("rb2610", 100, "buy", total_volume=1100))
    assert n == 0
    assert len(r.published) == 1


@pytest.mark.asyncio
async def test_publishes_separate_modes() -> None:
    """live / virtual 独立计数互不干扰"""
    r = _FakeRedis()
    await detect_and_publish(r, "live", _orderbook("rb2610", 10, "buy", total_volume=1000))
    await detect_and_publish(r, "virtual", _orderbook("rb2610", 10, "buy", total_volume=1000))
    n_live = await detect_and_publish(r, "live", _orderbook("rb2610", 100, "buy", total_volume=1100))
    n_virt = await detect_and_publish(r, "virtual", _orderbook("rb2610", 100, "buy", total_volume=1100))
    assert n_live == 1
    assert n_virt == 1
    channels = {ch for ch, _ in r.published}
    assert channels == {"ws:big-order:live", "ws:big-order:virtual"}


def test_baseline_is_reasonable() -> None:
    """基线为固定值（MVP）"""
    assert get_baseline() == 50
