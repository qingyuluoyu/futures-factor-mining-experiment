"""逐秒成交服务单测 —— collect_tick 提取逻辑（价格取整 + 四路开平拆分估算）"""

from __future__ import annotations

from app.services.notify.trade_tick_svc import (
    _round_price,
    collect_tick,
    split_open_close,
)


def _ob(
    symbol: str,
    current_volume,
    direction="",
    avg_price=3000.0,
    oi_delta=None,
) -> dict:
    return {
        "symbol": symbol,
        "last_direction": direction,
        "stats": {
            "current_volume": (
                {"value": current_volume, "source": "derived", "quality": "derived"}
                if current_volume is not None
                else None
            ),
            "oi_delta": (
                {"value": oi_delta, "source": "derived", "quality": "derived"}
                if oi_delta is not None
                else None
            ),
            "average_price": {"value": avg_price, "source": "derived", "quality": "derived"},
        },
    }


def _quote(symbol: str, last_price, decimal_places=0) -> dict:
    return {"symbol": symbol, "last_price": last_price, "decimal_places": decimal_places}


def test_collect_returns_none_when_no_volume() -> None:
    """该秒无成交（current_volume<=0 或 None）不提取"""
    assert collect_tick(_ob("rb2610", 0, "buy"), _quote("rb2610", 3250)) is None
    assert collect_tick(_ob("rb2610", None, "buy"), _quote("rb2610", 3250)) is None
    assert collect_tick(_ob("rb2610", -5, "buy"), _quote("rb2610", 3250)) is None


def test_collect_returns_none_when_no_symbol() -> None:
    assert collect_tick(_ob("", 100, "buy"), _quote("", 3250)) is None


def test_collect_uses_last_price_not_average() -> None:
    """价格取 quote.last_price（最新价），而非 orderbook 的 average_price"""
    t = collect_tick(
        _ob("rb2610", 120, "buy", avg_price=3250.555),
        _quote("rb2610", 3250),
    )
    assert t is not None
    assert t["price"] == 3250.0  # 螺纹钢 tick=1 → 整数
    assert t["volume"] == 120
    assert t["direction"] == "buy"


def test_collect_buysell() -> None:
    t = collect_tick(_ob("rb2610", 120, "buy"), _quote("rb2610", 3250))
    assert t == {
        "symbol": "rb2610",
        "direction": "buy",
        "volume": 120,
        "price": 3250.0,
        "open_long": None,
        "close_long": None,
        "open_short": None,
        "close_short": None,
    }
    t2 = collect_tick(_ob("MA2609", 80, "sell"), _quote("MA2609", 2500))
    assert t2 is not None
    assert t2["direction"] == "sell"


def test_collect_unknown_direction_becomes_empty() -> None:
    """方向非 buy/sell（含空）归一为空串，仍保留该秒成交"""
    t = collect_tick(_ob("rb2610", 50, ""), _quote("rb2610", 3250))
    assert t is not None
    assert t["direction"] == ""


def test_collect_symbol_lowercased() -> None:
    """symbol 统一小写，便于查询"""
    t = collect_tick(_ob("RB2610", 100, "buy"), _quote("RB2610", 3250))
    assert t is not None
    assert t["symbol"] == "rb2610"


def test_round_price_integer_for_rebar() -> None:
    """螺纹钢 tick=1，价格取整为整数（无小数）"""
    assert _round_price(3250.123, "rb2610") == 3250.0
    assert _round_price(3250.9, "rb2510") == 3251.0


def test_round_price_decimal_for_gold() -> None:
    """黄金 tick=0.02，保留 2 位小数"""
    p = _round_price(678.905, "au2510")
    # 678.905 / 0.02 = 33945.25 → round=33945 → 678.90
    assert p == 678.9


def test_collect_missing_price_defaults_zero() -> None:
    """无最新价时 price 兜底 0（不丢行）"""
    t = collect_tick(_ob("rb2610", 100, "buy"), {"symbol": "rb2610"})
    assert t is not None
    assert t["price"] == 0.0


# ===== 四路开平拆分（split_open_close）=====


def test_split_double_open() -> None:
    """双开：全部主动买开多，ΔOI=买量"""
    assert split_open_close(100, 0, 100) == (100, 0, 0, 0)


def test_split_double_close() -> None:
    """双平：全部主动卖平多，ΔOI=-卖量"""
    assert split_open_close(0, 80, -80) == (0, 80, 0, 0)


def test_split_position_turnover() -> None:
    """换手：ΔOI=0，买卖各对半拆开平"""
    assert split_open_close(50, 50, 0) == (25, 25, 25, 25)


def test_split_none_delta_returns_all_none() -> None:
    """持仓缺失（oi_delta=None）无法拆分"""
    assert split_open_close(100, 50, None) == (None, None, None, None)


def test_split_extreme_delta_all_open_or_all_close() -> None:
    """|ΔOI|=总成交 的边界：全开 / 全平"""
    # ΔOI=200=总成交 → 全部双开：买侧开多 100，卖侧开空 100
    assert split_open_close(100, 100, 200) == (100, 0, 100, 0)
    # ΔOI=-200 → 全部双平：买侧平空 100，卖侧平多 100
    assert split_open_close(100, 100, -200) == (0, 100, 0, 100)


def test_split_conserves_volume() -> None:
    """守恒：开多+平空=买量，开空+平多=卖量，净开平≈ΔOI（取整误差≤2手）"""
    for vol_buy, vol_sell, delta in [
        (37, 41, 13),
        (120, 80, -45),
        (99, 99, 0),
        (7, 13, 7),
        (13, 7, 7),
        (100, 100, -150),
    ]:
        open_long, close_long, open_short, close_short = split_open_close(
            vol_buy, vol_sell, delta
        )
        assert open_long + close_short == vol_buy
        assert open_short + close_long == vol_sell
        net = open_long + open_short - close_long - close_short
        assert abs(net - delta) <= 2


def test_collect_with_oi_delta_splits_four_ways() -> None:
    """direction + oi_delta 齐备时产出四路估算"""
    t = collect_tick(
        _ob("rb2610", 100, "buy", oi_delta=100), _quote("rb2610", 3250)
    )
    assert t is not None
    assert t["open_long"] == 100
    assert t["close_long"] == 0
    assert t["open_short"] == 0
    assert t["close_short"] == 0


def test_collect_sell_with_oi_delta_splits_four_ways() -> None:
    """主动卖 + 持仓增 → 开空"""
    t = collect_tick(
        _ob("rb2610", 80, "sell", oi_delta=80), _quote("rb2610", 3250)
    )
    assert t is not None
    assert t["open_short"] == 80
    assert t["close_long"] == 0


def test_collect_without_oi_delta_keeps_none() -> None:
    """oi_delta 缺失（sina 源/数据异常）→ 四路 None，volume 照存"""
    t = collect_tick(_ob("rb2610", 100, "buy"), _quote("rb2610", 3250))
    assert t is not None
    assert t["open_long"] is None
    assert t["close_short"] is None


def test_collect_unknown_direction_skips_split() -> None:
    """方向不明时即使有 oi_delta 也不拆（整秒无法归属买卖）"""
    t = collect_tick(
        _ob("rb2610", 50, "", oi_delta=50), _quote("rb2610", 3250)
    )
    assert t is not None
    assert t["direction"] == ""
    assert t["open_long"] is None
    assert t["open_short"] is None
