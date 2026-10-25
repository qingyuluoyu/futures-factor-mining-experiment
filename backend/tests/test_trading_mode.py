"""交易盘模式与行情键单元测试"""

from __future__ import annotations

from app.core.trading_mode import (
    DEFAULT_MODE,
    MODE_LIVE,
    MODE_VIRTUAL,
    mode_label_zh,
    normalize_trading_mode,
)
from app.services.market_keys import (
    quote_key,
    write_keys_for_quote,
    channel_key,
    default_channel_for_mode,
)


def test_normalize_trading_mode() -> None:
    assert normalize_trading_mode(None) == MODE_LIVE
    assert normalize_trading_mode("") == DEFAULT_MODE
    assert normalize_trading_mode("LIVE") == MODE_LIVE
    assert normalize_trading_mode("virtual") == MODE_VIRTUAL
    assert normalize_trading_mode("xxx") == MODE_LIVE


def test_mode_label_zh() -> None:
    assert "实" in mode_label_zh("live")
    assert "虚" in mode_label_zh("virtual")


def test_market_keys_isolation() -> None:
    assert quote_key("live", "rb2610") == "market:live:quote:rb2610"
    assert quote_key("virtual", "rb2610") == "market:virtual:quote:rb2610"
    live_keys = write_keys_for_quote("live", "rb2610")
    assert "market:live:quote:rb2610" in live_keys
    assert "market:quote:rb2610" in live_keys  # 兼容旧键
    virt_keys = write_keys_for_quote("virtual", "rb2610")
    assert virt_keys == ["market:virtual:quote:rb2610"]
    assert channel_key("virtual") == "market:virtual:channel"
    assert default_channel_for_mode("virtual") == "openctp_724"
    # virtual 不双写旧键，避免污染实盘
    assert all("virtual" in k for k in virt_keys)


def test_kline_keys_isolation() -> None:
    from app.services.kline_keys import (
        history_kline_key,
        realtime_kline_key,
        realtime_kline_scan_pattern,
    )

    assert realtime_kline_key("live", "rb2610", "1m") == "kline:realtime:rb2610:1m"
    assert (
        realtime_kline_key("virtual", "rb2610", "1m")
        == "kline:virtual:realtime:rb2610:1m"
    )
    assert history_kline_key("live", "rb2610", "1m") == "kline:history:rb2610:1m"
    assert (
        history_kline_key("virtual", "rb2610", "1m")
        == "kline:virtual:history:rb2610:1m"
    )
    assert realtime_kline_scan_pattern("virtual") == "kline:virtual:realtime:*"


def test_virtual_history_dedupe_merge() -> None:
    from app.services.kline_virtual_hist import (
        _dedupe,
        _merge_forming,
        _needs_reseed,
    )

    bars = [
        {"time": "2026-07-25 12:00:00", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 1},
        {"time": "2026-07-25 12:00:00", "open": 1, "high": 3, "low": 1, "close": 2, "volume": 2},
    ]
    d = _dedupe(bars, "1m")
    assert len(d) == 1
    assert d[0]["close"] == 2
    merged = _merge_forming(
        [{"time": "2026-07-25 12:01:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}],
        {"time": "2026-07-25 12:01:00", "open": 1, "high": 5, "low": 1, "close": 4, "volume": 3, "source": "virtual"},
        "1m",
    )
    assert merged[-1]["close"] == 4
    assert merged[-1]["high"] == 5
    # 无 seed 或过薄 → 需要重种
    assert _needs_reseed([], "1m") is True
    thin = [{"time": f"2026-07-25 12:{i:02d}:00", "source": "virtual", "close": 1} for i in range(10)]
    assert _needs_reseed(thin, "1m") is True
    fat = [
        {"time": f"2026-07-25 10:{i:02d}:00", "source": "seed", "close": 1}
        for i in range(50)
    ] + [
        {"time": f"2026-07-25 11:{i:02d}:00", "source": "seed", "close": 1}
        for i in range(50)
    ] + [
        {"time": f"2026-07-25 12:{i:02d}:00", "source": "seed", "close": 1}
        for i in range(50)
    ] + [
        {"time": f"2026-07-25 13:{i:02d}:00", "source": "seed", "close": 1}
        for i in range(50)
    ]
    assert len(fat) >= 200
    assert _needs_reseed(fat, "1m") is False


def test_virtual_trades_on_price_tick() -> None:
    from app.services.virtual_market_side import generate_trades_virtual

    prev = {"symbol": "rb2610", "last_price": 3070.0, "volume": 100}
    cur = {"symbol": "rb2610", "last_price": 3072.0, "volume": 100}
    # 量不变但价变 → virtual 仍出 1 手
    trades = generate_trades_virtual(cur, prev)
    assert len(trades) == 1
    assert trades[0]["direction"] == "buy"
    assert trades[0]["volume"] == 1
    assert trades[0]["source"] == "price_tick"
    # 量增仍走 volume_delta
    cur2 = {"symbol": "rb2610", "last_price": 3072.0, "volume": 105}
    t2 = generate_trades_virtual(cur2, prev)
    assert t2 and t2[0]["source"] == "volume_delta"
    assert sum(x["volume"] for x in t2) == 5


def test_virtual_quote_pulse_stale_logic() -> None:
    """锚价脉冲：同价超时才 pulse；价只绕 anchor ±1 跳，不随机游走"""
    from datetime import datetime
    from app.services import virtual_quote_pulse as pulse

    pulse._last_seen.clear()
    sym = "rb2610"
    assert pulse.should_pulse(sym, 3070.0, 100, 100.0, 25.0) is True
    pulse.note_seen(sym, 3070.0, 100, 100.0)
    assert pulse.should_pulse(sym, 3070.0, 100, 110.0, 25.0) is False
    assert pulse.should_pulse(sym, 3070.0, 100, 130.0, 25.0) is True
    assert pulse.should_pulse(sym, 3071.0, 100, 130.0, 25.0) is False

    q = {
        "symbol": sym,
        "last_price": 3456.0,  # 已漂移的脏 last
        "anchor_price": 3076.0,
        "bid_price": 3075.0,
        "ask_price": 3077.0,
        "volume": 100,
        "pre_close": 3090.0,
        "source": "ctp:openctp_724+pulse",
        "decimal_places": 0,
    }
    now = datetime(2026, 7, 25, 16, 30, 0)
    # 强制多次：价必须始终在 anchor±1
    for _ in range(30):
        out = pulse.build_pulsed_quote(q, now, anchor=3076.0)
        assert out is not None
        assert out["tick_time"] == "163000"
        assert "pulse" in str(out.get("source"))
        assert abs(float(out["last_price"]) - 3076.0) <= 1.0 + 1e-9
        assert float(out["anchor_price"]) == 3076.0
    assert pulse.resolve_anchor(q) == 3076.0
    assert pulse.resolve_anchor({"last_price": 1, "source": "ctp:x+pulse"}) == 0.0


def test_virtual_kline_period_bucket_wall_clock() -> None:
    """虚拟盘 bar 时间按墙钟周期起点，14:30 的 60m 应为 14:00 而非 15:00"""
    from datetime import datetime
    from app.services.kline_virtual import _period_bucket

    now = datetime(2026, 7, 25, 14, 30, 12)
    assert _period_bucket("1m", now).strftime("%H:%M") == "14:30"
    assert _period_bucket("5m", now).strftime("%H:%M") == "14:30"
    assert _period_bucket("15m", now).strftime("%H:%M") == "14:30"
    assert _period_bucket("30m", now).strftime("%H:%M") == "14:30"
    assert _period_bucket("60m", now).strftime("%H:%M") == "14:00"
    assert _period_bucket("1d", now).strftime("%Y-%m-%d") == "2026-07-25"
    # 14:31 → 1m 桶 14:31；60m 仍 14:00
    now2 = datetime(2026, 7, 25, 14, 31, 0)
    assert _period_bucket("1m", now2).strftime("%H:%M") == "14:31"
    assert _period_bucket("60m", now2).strftime("%H:%M") == "14:00"


def test_bridge_heartbeat_parse() -> None:
    from app.connectors.ctp.bridge_heartbeat import parse_heartbeat
    import time

    raw = f"{time.time()}|openctp_724|virtual"
    h = parse_heartbeat(raw)
    assert h["ok"] is True
    assert h["channel"] == "openctp_724"
    assert parse_heartbeat(None)["ok"] is False


def test_czce_instrument_aliases() -> None:
    from app.connectors.ctp.instrument_map import (
        expand_symbol_aliases,
        resolve_canonical_symbol,
        build_instrument_to_canonical,
        build_subscribe_instruments,
        expand_product_month_instruments,
    )
    from app.connectors.ctp.normalize import depth_to_quote

    aliases = expand_symbol_aliases("cf2701", "郑商所")
    assert "cf2701" in [a.lower() for a in aliases]
    assert "cf701" in [a.lower() for a in aliases]
    m = build_instrument_to_canonical()
    assert resolve_canonical_symbol("CF701", m) == "cf2701"
    assert resolve_canonical_symbol("cf2701", m) == "cf2701"
    # 不同月禁止映射到主合约（否则 K 线串价）
    assert resolve_canonical_symbol("cu2609", m) == "cu2609"
    assert resolve_canonical_symbol("cu2607", m) == "cu2607"
    # depth_to_quote 丢弃非表内月份
    assert (
        depth_to_quote(
            {"InstrumentID": "cu2609", "LastPrice": 90000},
            {c["symbol"].lower(): c for c in __import__("app.data.contracts", fromlist=["ALL_CONTRACTS"]).ALL_CONTRACTS},
        )
        is None
    )
    months = expand_product_month_instruments("cu")
    assert any(x.lower().startswith("cu26") for x in months)
    subs = build_subscribe_instruments()
    assert len(subs) > 200
    assert any(s.upper() == "CF701" for s in subs)
