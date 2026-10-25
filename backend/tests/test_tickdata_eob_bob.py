"""pg-tickdata eob（周期终点）→ bob（周期起点）读取换算回归

2026-08-25 前读取未换算：夜盘末根 eob"23:00" 与主链 bob"22:45" 双标签
并存——夜盘缓存整段终点标签、开盘刷新后重建起点标签，图面表现为
「23:00 的 K 线消失」+ 相邻 bar 错位一格。
"""

from datetime import datetime

from app.data.tickdata_kline import _eob_to_bob, _rows_to_bars


def _row(ts: str, o=100.0, h=101.0, l=99.0, c=100.5, vol=10, pos=5):
    return {
        "ts": datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": vol,
        "position": pos,
    }


def test_15m_night_eob_rows_shift_to_bob_labels():
    """夜盘 15m eob 行（21:15..23:00）→ bob 标签（21:00..22:45）。"""
    rows_desc = [
        _row("2026-08-24 23:00:00", o=8156, h=8159, l=8144, c=8147, vol=12713),
        _row("2026-08-24 22:45:00", o=8148, h=8167, l=8141, c=8156, vol=17251),
        _row("2026-08-24 22:30:00", o=8179, h=8185, l=8137, c=8148, vol=26050),
    ]
    bars = _rows_to_bars(rows_desc, "15m", "pp2701")
    times = [b["time"] for b in bars]
    assert times == [
        "2026-08-24 22:15:00",
        "2026-08-24 22:30:00",
        "2026-08-24 22:45:00",
    ]
    last = bars[-1]
    assert (last["open"], last["high"], last["low"], last["close"]) == (
        8156, 8159, 8144, 8147,
    )


def test_1m_close_print_row_folds_into_last_minute():
    """vvtr 的 1m 收盘打印行（eob 23:01 → bob 22:59）折叠进真实末根。"""
    rows_desc = [
        _row("2026-08-24 23:01:00", o=8138, h=8138, l=8138, c=8138, vol=320),
        _row("2026-08-24 23:00:00", o=8137, h=8139, l=8136, c=8138, vol=1016),
        _row("2026-08-24 22:59:00", o=8136, h=8138, l=8135, c=8137, vol=751),
    ]
    bars = _rows_to_bars(rows_desc, "1m", "pp2701")
    times = [b["time"] for b in bars]
    assert times == ["2026-08-24 22:58:00", "2026-08-24 22:59:00"]
    last = bars[-1]
    assert last["open"] == 8137  # 保留真实末根开盘
    assert last["close"] == 8138  # 收盘打印为最终收盘
    assert last["high"] == 8139
    assert last["low"] == 8136
    assert last["volume"] == 1016 + 320


def test_spanning_60m_bucket_maps_back_to_axis_start():
    """AU 跨夜到日盘的 60m 末桶（02:00-09:30，eob 09:30）→ 起点 02:00。"""
    assert _eob_to_bob(
        datetime(2026, 8, 25, 9, 30), "60m", "au2612"
    ) == datetime(2026, 8, 25, 2, 0)


def test_day_30m_eob_crossing_break_maps_to_axis_start():
    """跨小休的 30m 桶（10:00-10:15+10:30-10:45，eob 10:45）→ 起点 10:00。"""
    assert _eob_to_bob(
        datetime(2026, 8, 25, 10, 45), "30m", "rb2610"
    ) == datetime(2026, 8, 25, 10, 0)


def test_day_open_15m_eob_maps_to_open_bucket():
    assert _eob_to_bob(
        datetime(2026, 8, 25, 9, 15), "15m", "rb2610"
    ) == datetime(2026, 8, 25, 9, 0)


def test_unknown_instrument_falls_back_to_plain_shift():
    assert _eob_to_bob(datetime(2026, 8, 24, 23, 0), "15m", "") == datetime(
        2026, 8, 24, 22, 45
    )


def test_daily_not_converted():
    assert _eob_to_bob(datetime(2026, 8, 25, 0, 0), "1d", "rb2610") == datetime(
        2026, 8, 25, 0, 0
    )
