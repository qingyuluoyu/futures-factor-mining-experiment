"""VVTR K线解析 + merge 主源标签 + 配置状态镜像 单测"""

from app.connectors.vvtr.http_client import _lookback_days, parse_history_response
from app.connectors.vvtr.quote_map import vvtr_ws_message_to_quote
from app.services.market_quote_merge import merge_live_quote, merge_live_quotes


def test_lookback_days_tight_window_for_minutes():
    """分钟线窗口按根数收紧：1024 根 1m ≈ 5 天内（防取到窗口头部旧数据）"""
    assert _lookback_days("1m", 1024) <= 6
    assert _lookback_days("1m", 500) <= 4
    assert _lookback_days("15m", 1024) <= 38
    assert _lookback_days("1h", 1024) <= 148


def test_lookback_days_caps_and_daily():
    # 窗口上限按官方规则分档：<15m 半年，15m~1d 两年，日线全历史
    assert _lookback_days("1m", 10**9) == 180
    assert _lookback_days("5m", 10**9) == 180
    assert _lookback_days("15m", 10**9) == 730
    assert _lookback_days("1h", 10**9) == 730
    # 日线按 65% 交易日占比 + 10 天缓冲
    assert 100 <= _lookback_days("1D", 500) <= 800
    assert _lookback_days("1D", 10**9) == 3650


def _vvtr_quote(**overrides):
    quote = vvtr_ws_message_to_quote(
        {
            "t": "20251219101500000",
            "s": "rb2610",
            "p": 3100,
            "total_vol": 3245,
            "open": 3090,
            "high": 3110,
            "low": 3080,
            "pre_close": 3085,
            "open_int": 1442559,
            "q": [{"bP": 3099, "bV": 50, "aP": 3100, "aV": 60}],
        }
    )
    assert quote is not None
    quote["recv_ts"] = 1000.0
    quote.update(overrides)
    return quote


def _sina_quote(**overrides):
    quote = {
        "symbol": "rb2610",
        "name": "螺纹钢2610",
        "exchange": "上期所",
        "last_price": 3095.0,
        "open_price": 3088.0,
        "high_price": 3108.0,
        "low_price": 3078.0,
        "pre_close": 3084.0,
        "bid_price": 3094.0,
        "ask_price": 3096.0,
        "volume": 3200,
        "position": 1440000,
        "trade_date": "2025-12-19",
        "recv_ts": 1002.0,
        "source": "sina",
    }
    quote.update(overrides)
    return quote


def test_vvtr_primary_wins_and_labelled():
    merged = merge_live_quote(
        _vvtr_quote(), _sina_quote(), now_ts=1005.0, primary_source="vvtr"
    )
    assert merged is not None
    assert merged["last_price"] == 3100
    assert merged["source"] == "vvtr"
    assert merged["field_meta"]["last_price"]["source"] == "vvtr"
    # 5 档来自 vvtr（sina 无档位）
    assert merged["bid_levels"][0]["price"] == 3099
    assert merged["recv_ts"] == 1000.0


def test_vvtr_stale_falls_back_to_sina():
    merged = merge_live_quote(
        _vvtr_quote(recv_ts=0.0),  # 1005-0 远超 90s 新鲜度
        _sina_quote(),
        now_ts=1005.0,
        primary_source="vvtr",
    )
    assert merged is not None
    assert merged["last_price"] == 3095.0
    assert merged["source"] == "sina"


def test_vvtr_trade_date_behind_sina_marked_stale():
    # VVTR 交易日落后（如夜盘归属推算滞后）→ 新鲜度判否 → sina 兜底
    merged = merge_live_quote(
        _vvtr_quote(trade_date="2025-12-18"),
        _sina_quote(trade_date="2025-12-19"),
        now_ts=1005.0,
        primary_source="vvtr",
    )
    assert merged is not None
    assert merged["last_price"] == 3095.0


def test_merge_live_quotes_batch_with_vvtr_primary():
    out = merge_live_quotes(
        [_vvtr_quote()],
        [_sina_quote()],
        [],
        now_ts=1005.0,
        primary_source="vvtr",
    )
    assert len(out) == 1
    assert out[0]["symbol"] == "rb2610"
    assert out[0]["source"] == "vvtr"


# ---------- K线响应解析 ----------


def _history_payload():
    return {
        "code": 200,
        "msg": "ok",
        "data": {
            "hasMore": False,
            "cursorToken": "",
            "records": [
                [
                    {
                        "s": "rb2610",
                        "t": 1776063540,
                        "o": 3100,
                        "h": 3101,
                        "l": 3099,
                        "c": 3100,
                        "v": 3245,
                        "a": 100605000,
                        "b": "2026-04-13 14:59:00",
                        "e": "2026-04-13 15:00:00",
                    },
                    {
                        "s": "rb2610",
                        "t": 1776063480,
                        "o": 3101,
                        "h": 3101,
                        "l": 3099,
                        "c": 3100,
                        "v": 2625,
                        "a": 81379930,
                        "b": "2026-04-13 14:58:00",
                        "e": "2026-04-13 14:59:00",
                    },
                ]
            ],
        },
    }


def test_parse_history_response_sorts_by_time_and_uses_bob():
    bars = parse_history_response(_history_payload(), "rb2610")
    assert len(bars) == 2
    # 按 time 升序（bob，bar 起点，与新浪分钟 bar 语义一致）
    assert bars[0]["time"] == "2026-04-13 14:58:00"
    assert bars[1]["time"] == "2026-04-13 14:59:00"
    assert bars[0]["open"] == 3101
    assert bars[0]["volume"] == 2625


def test_parse_history_response_filters_invalid_bars():
    payload = _history_payload()
    payload["data"]["records"][0].append(
        {"s": "rb2610", "o": 0, "c": 0, "v": 0, "b": "2026-04-13 15:01:00"}
    )
    bars = parse_history_response(payload, "rb2610")
    assert len(bars) == 2


def test_parse_history_response_ignores_other_symbols():
    payload = _history_payload()
    payload["data"]["records"].append(
        [
            {
                "s": "hc2610",
                "o": 3200,
                "h": 3210,
                "l": 3190,
                "c": 3205,
                "v": 100,
                "b": "2026-04-13 14:59:00",
            }
        ]
    )
    bars = parse_history_response(payload, "rb2610")
    assert all(b["time"] for b in bars)
    assert len(bars) == 2


# ---------- 多端共享 key：同步 owner 开关 ----------


async def test_sync_loop_disabled_by_env(monkeypatch):
    """VVTR_TICKDATA_SYNC_DISABLED=1 → 循环立即退出（不拉取、不轮询）"""
    import asyncio

    from app.services.vvtr_tickdata_sync import vvtr_tickdata_sync_loop

    monkeypatch.setenv("VVTR_TICKDATA_SYNC_DISABLED", "1")
    # 若开关失效会进入常驻循环（拉取+长眠），wait_for 超时即失败
    await asyncio.wait_for(vvtr_tickdata_sync_loop(None), timeout=2)
