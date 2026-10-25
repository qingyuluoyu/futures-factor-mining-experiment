"""技术面信号与分批单元测试"""

from __future__ import annotations

from app.services.ai.research.chunking import chunk_list, clamp_batch_size, make_agent_name
from app.services.ai.research.signals import score_product
from app.services.ai.research.types import ProductRef


def _trend_bars(start: float, factor: float, n: int) -> list[dict]:
    bars: list[dict] = []
    price = start
    for i in range(n):
        price = price * factor
        bars.append(
            {
                "time": str(i),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000 + i,
                "open_interest": 5000 + i * 10,
            }
        )
    return bars


def test_score_uptrend_long() -> None:
    bars = _trend_bars(100.0, 1.01, 60)
    sig = score_product(
        "RB",
        "rb2610",
        "螺纹",
        bars,
        {"last_price": bars[-1]["close"], "change_percent": 2.0},
    )
    assert sig["bias"] == "long"
    assert sig["direction_zh"] == "做多"
    assert sig["action"] == "买入开多"
    assert sig["entry"] is not None
    assert sig["stop_loss"] is not None
    assert sig["take_profit"] is not None
    assert sig["support"] is not None
    assert sig["resistance"] is not None
    # 做多：止损应低于入场，止盈应高于入场
    assert sig["stop_loss"] < sig["entry"]
    assert sig["take_profit"] > sig["entry"]


def test_score_downtrend_short() -> None:
    bars = _trend_bars(200.0, 0.99, 60)
    sig = score_product(
        "I",
        "i2609",
        "铁矿",
        bars,
        {"last_price": bars[-1]["close"], "change_percent": -2.0},
    )
    assert sig["bias"] == "short"
    assert sig["direction_zh"] == "做空"
    assert sig["action"] == "卖出开空"
    assert sig["entry"] is not None
    assert sig["stop_loss"] is not None
    assert sig["take_profit"] is not None
    assert sig["stop_loss"] > sig["entry"]
    assert sig["take_profit"] < sig["entry"]


def test_score_flat_range() -> None:
    bars: list[dict] = []
    for i in range(60):
        p = 100 + (i % 5) * 0.2
        bars.append(
            {
                "time": str(i),
                "open": p,
                "high": p + 0.3,
                "low": p - 0.3,
                "close": p,
                "volume": 100,
                "open_interest": 1000,
            }
        )
    sig = score_product("AU", "au2612", "黄金", bars, {"last_price": 100.0, "change_percent": 0.0})
    assert sig["bias"] == "range"


def test_chunk_and_name() -> None:
    assert clamp_batch_size(3) == 5
    assert clamp_batch_size(20) == 10
    assert clamp_batch_size(None) == 8
    items: list[ProductRef] = [
        {"product_code": f"C{i}", "symbol": f"c{i}", "product_name": f"C{i}"}
        for i in range(20)
    ]
    batches = chunk_list(items, 8)
    assert len(batches) == 3
    name = make_agent_name(1, batches[0])
    assert name.startswith("子代理-1")
