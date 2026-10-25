"""日线 bar 时间去重：历史 YYYY-MM-DD 与 realtime 00:00:00 不得并存"""

from app.services.kline import _bar_time_key, _dedupe_bars_by_time


def test_bar_time_key_daily_strips_clock():
    assert _bar_time_key("1d", "2026-07-23") == "2026-07-23"
    assert _bar_time_key("1d", "2026-07-23 00:00:00") == "2026-07-23"
    assert _bar_time_key("1d", "2026-07-23 09:00:00") == "2026-07-23"


def test_bar_time_key_minute_keeps_full():
    assert _bar_time_key("1m", "2026-07-23 10:05:00") == "2026-07-23 10:05:00"


def test_dedupe_daily_prefers_later_bar():
    bars = [
        {
            "time": "2026-07-22",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 1.5,
            "volume": 10,
        },
        {
            "time": "2026-07-23",
            "open": 3083,
            "high": 3108,
            "low": 3078,
            "close": 3085,
            "volume": 833566,
            "open_interest": 1872417,
        },
        {
            "time": "2026-07-23 00:00:00",
            "open": 3083,
            "high": 3108,
            "low": 3078,
            "close": 3085,
            "volume": 1872417,
            "open_interest": None,
        },
    ]
    out = _dedupe_bars_by_time(bars, "1d")
    assert len(out) == 2
    assert out[-1]["time"] == "2026-07-23 00:00:00"
    assert out[-1]["volume"] == 1872417
