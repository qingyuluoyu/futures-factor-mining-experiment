"""服务端统一盘口统计。"""

import pytest

from app.services.market_panel import build_market_panel


def _quote(**overrides):
    quote = {
        "symbol": "pp2609",
        "source": "simnow",
        "recv_ts": 1000.0,
        "trade_date": "20260729",
        "last_price": 8500.0,
        "open_price": 8450.0,
        "high_price": 8600.0,
        "low_price": 8400.0,
        "pre_close": 8420.0,
        "bid_price": 8499.0,
        "ask_price": 8501.0,
        "bid_vol": 10,
        "ask_vol": 12,
        "bid_levels": [
            {"price": 8499.0, "volume": 10},
            {"price": 8498.0, "volume": 20},
        ],
        "ask_levels": [
            {"price": 8501.0, "volume": 12},
            {"price": 8502.0, "volume": 22},
        ],
        "volume": 1000,
        "position": 400000,
        "pre_open_interest": 390000,
        "turnover": 42_500_000.0,
        "settlement_price": None,
        "upper_limit_price": 9240.0,
        "lower_limit_price": 7560.0,
        "field_meta": {
            "open_price": {"source": "simnow", "quality": "direct"},
            "high_price": {"source": "simnow", "quality": "direct"},
            "low_price": {"source": "simnow", "quality": "direct"},
            "pre_close": {"source": "simnow", "quality": "direct"},
            "position": {"source": "simnow", "quality": "direct"},
            "pre_open_interest": {"source": "simnow", "quality": "direct"},
            "turnover": {"source": "simnow", "quality": "direct"},
            "settlement_price": {"source": "missing", "quality": "missing"},
            "upper_limit_price": {"source": "simnow", "quality": "direct"},
            "lower_limit_price": {"source": "simnow", "quality": "direct"},
            "volume": {"source": "simnow", "quality": "direct"},
        },
    }
    quote.update(overrides)
    return quote


def _previous_panel(**overrides):
    panel = {
        "symbol": "pp2609",
        "trade_date": "20260729",
        "last_direction": "buy",
        "stats": {
            "outer_volume": {
                "value": 100,
                "source": "derived",
                "quality": "estimated",
            },
            "inner_volume": {
                "value": 50,
                "source": "derived",
                "quality": "estimated",
            },
        },
    }
    panel.update(overrides)
    return panel


def test_builds_depth_and_exact_daily_statistics():
    panel = build_market_panel(
        _quote(),
        _quote(last_price=8499.0, volume=990),
        _previous_panel(),
        now_ts=1005.0,
    )

    assert panel["asks"] == _quote()["ask_levels"]
    assert panel["bids"] == _quote()["bid_levels"]
    assert panel["source"] == "simnow"
    assert panel["stale"] is False
    assert panel["stats"]["daily_oi_change"]["value"] == 10000
    assert panel["stats"]["daily_oi_change"]["quality"] == "derived"
    assert panel["stats"]["amplitude"]["value"] == pytest.approx(
        (8600 - 8400) / 8420 * 100
    )
    assert panel["stats"]["average_price"]["value"] == 8500.0
    assert panel["stats"]["turnover"]["value"] == 42_500_000.0
    assert panel["stats"]["current_volume"] == {
        "value": 10,
        "source": "derived",
        "quality": "estimated",
    }
    assert panel["stats"]["outer_volume"]["value"] == 110
    assert panel["stats"]["inner_volume"]["value"] == 50


def test_missing_values_are_explicit_and_turnover_can_be_estimated():
    panel = build_market_panel(
        _quote(turnover=None, volume=100),
        None,
        None,
        now_ts=1005.0,
    )

    assert panel["stats"]["turnover"] == {
        "value": 4_250_000.0,
        "source": "derived",
        "quality": "estimated",
    }
    assert panel["stats"]["average_price"]["value"] == 8500.0
    assert panel["stats"]["average_price"]["quality"] == "estimated"
    for field in ("settlement_price", "volume_ratio", "spot", "basis"):
        assert panel["stats"][field] == {
            "value": None,
            "source": "missing",
            "quality": "missing",
        }


def test_inner_outer_state_resets_on_new_trading_day():
    panel = build_market_panel(
        _quote(trade_date="20260730", volume=5),
        _quote(trade_date="20260729", volume=1000),
        _previous_panel(),
        now_ts=1005.0,
    )

    assert panel["stats"]["current_volume"]["value"] is None
    assert panel["stats"]["outer_volume"]["value"] == 0
    assert panel["stats"]["inner_volume"]["value"] == 0


def test_same_price_uses_previous_tick_direction_for_volume_classification():
    panel = build_market_panel(
        _quote(last_price=8500.0, volume=1010, bid_price=8499, ask_price=8501),
        _quote(last_price=8500.0, volume=1000),
        _previous_panel(last_direction="sell"),
        now_ts=1005.0,
    )

    assert panel["last_direction"] == "sell"
    assert panel["stats"]["outer_volume"]["value"] == 100
    assert panel["stats"]["inner_volume"]["value"] == 60


# ===== 单秒持仓变化（oi_delta，四路开平拆分原料）=====


def test_oi_delta_derived_same_trading_day():
    """同交易日：ΔOI = 本秒持仓 - 上秒持仓（|ΔOI|≤单秒成交）"""
    panel = build_market_panel(
        _quote(volume=1010, position=400006),
        _quote(volume=1000, position=400000),
        _previous_panel(),
        now_ts=1005.0,
    )
    assert panel["stats"]["oi_delta"] == {
        "value": 6,
        "source": "derived",
        "quality": "estimated",
    }


def test_oi_delta_missing_when_position_absent():
    """上秒持仓缺失 → 不产出（不猜 0）"""
    panel = build_market_panel(
        _quote(volume=1010, position=400060),
        _quote(volume=1000, position=None),
        _previous_panel(),
        now_ts=1005.0,
    )
    assert panel["stats"]["oi_delta"]["value"] is None


def test_oi_delta_dropped_when_exceeds_volume():
    """|ΔOI| > 单秒成交 → 源数据异常，丢弃（防切换源后持仓跳变污染拆分）"""
    panel = build_market_panel(
        _quote(volume=1010, position=402000),
        _quote(volume=1000, position=400000),
        _previous_panel(),
        now_ts=1005.0,
    )
    # current_volume=10，ΔOI=2000 远超成交量
    assert panel["stats"]["oi_delta"]["value"] is None


def test_oi_delta_zero_when_position_flat():
    """持仓不变（换手）→ ΔOI=0 有效（可拆分为对半开平）"""
    panel = build_market_panel(
        _quote(volume=1010, position=400000),
        _quote(volume=1000, position=400000),
        _previous_panel(),
        now_ts=1005.0,
    )
    assert panel["stats"]["oi_delta"]["value"] == 0


def test_oi_delta_missing_across_trading_day():
    """跨交易日（夜盘切换）→ 无上秒基准，missing"""
    panel = build_market_panel(
        _quote(trade_date="20260730", volume=10, position=400000),
        _quote(trade_date="20260729", volume=1000, position=400000),
        _previous_panel(),
        now_ts=1005.0,
    )
    assert panel["stats"]["current_volume"]["value"] is None
    assert panel["stats"]["oi_delta"]["value"] is None

