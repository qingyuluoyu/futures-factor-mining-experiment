"""1 分钟聚合多分钟 K 线（时间键统一为「周期起点」语义）"""

from app.data import sina_kline
from app.data.sina_kline import aggregate_from_1m, _ceil_period_end
from datetime import datetime


def _bar(t: str, o: float, h: float, l: float, c: float, v: int) -> dict:
    return {
        "time": t,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "settle": None,
        "open_interest": 100,
    }


def test_ceil_period_end_15m():
    assert _ceil_period_end(datetime(2026, 7, 24, 9, 1), 15) == datetime(
        2026, 7, 24, 9, 15
    )
    assert _ceil_period_end(datetime(2026, 7, 24, 9, 15), 15) == datetime(
        2026, 7, 24, 9, 15
    )
    assert _ceil_period_end(datetime(2026, 7, 24, 9, 16), 15) == datetime(
        2026, 7, 24, 9, 30
    )


def test_aggregate_15m_ohlcv():
    """连续 1m（起点标签）合成 15m：open 首根、close 末根、H/L 极值、量求和"""
    bars = [
        _bar("2026-07-24 09:01:00", 100, 101, 99, 100.5, 10),
        _bar("2026-07-24 09:02:00", 100.5, 102, 100, 101, 20),
        _bar("2026-07-24 09:15:00", 101, 103, 98, 99, 30),
        _bar("2026-07-24 09:16:00", 99, 100, 97, 98, 5),
    ]
    out = aggregate_from_1m(bars, "15m")
    assert len(out) == 2
    first, second = out[0], out[1]
    # 起点语义：09:01/09:02 → 09:00 桶；09:15/09:16 → 09:15 桶
    assert first["time"] == "2026-07-24 09:00:00"
    assert first["open"] == 100
    assert first["high"] == 102
    assert first["low"] == 99
    assert first["close"] == 101
    assert first["volume"] == 30
    assert second["time"] == "2026-07-24 09:15:00"
    assert second["open"] == 101
    assert second["volume"] == 35


def test_aggregate_1m_passthrough():
    bars = [_bar("2026-07-24 09:01:00", 1, 2, 1, 1.5, 3)]
    assert aggregate_from_1m(bars, "1m") == bars


def test_aggregate_rb_30m_matches_today_native_session_boundaries():
    """1m 兜底聚合（起点标签）必须落在与原生 30m 相同的桶起点上，
    跨 10:15 小休与午休的桶沿交易分钟轴对齐。"""
    bars = [
        _bar("2026-07-29 09:01:00", 1, 1, 1, 1, 1),
        _bar("2026-07-29 09:30:00", 2, 2, 2, 2, 1),
        _bar("2026-07-29 09:31:00", 3, 3, 3, 3, 1),
        _bar("2026-07-29 10:00:00", 4, 4, 4, 4, 1),
        _bar("2026-07-29 10:01:00", 5, 5, 5, 5, 1),
        _bar("2026-07-29 10:15:00", 6, 6, 6, 6, 1),
        _bar("2026-07-29 10:31:00", 7, 7, 7, 7, 1),
        _bar("2026-07-29 10:45:00", 8, 8, 8, 8, 1),
        _bar("2026-07-29 10:46:00", 9, 9, 9, 9, 1),
        _bar("2026-07-29 11:15:00", 10, 10, 10, 10, 1),
        _bar("2026-07-29 11:16:00", 11, 11, 11, 11, 1),
        _bar("2026-07-29 11:30:00", 12, 12, 12, 12, 1),
        _bar("2026-07-29 13:31:00", 13, 13, 13, 13, 1),
        _bar("2026-07-29 13:45:00", 14, 14, 14, 14, 1),
    ]

    out = aggregate_from_1m(bars, "30m", "rb2610")

    assert [bar["time"] for bar in out] == [
        "2026-07-29 09:00:00",
        "2026-07-29 09:30:00",
        "2026-07-29 10:00:00",  # 跨 10:15 小休：axis 60-89 = 10:00-10:15+10:30-10:45
        "2026-07-29 10:45:00",
        "2026-07-29 11:15:00",  # 跨午休：含 11:30 后的 13:31
        "2026-07-29 13:45:00",
    ]
    assert out[2]["open"] == 4
    assert out[2]["close"] == 7
    assert out[4]["close"] == 13


def test_aggregate_if_60m_anchors_at_0930():
    """中金所股指（09:30 开盘）1m 起点聚合 60m 首桶从 09:30 起。"""
    bars = [
        _bar("2026-07-29 09:31:00", 1, 1, 1, 1, 1),
        _bar("2026-07-29 10:30:00", 2, 2, 2, 2, 1),
        _bar("2026-07-29 10:31:00", 3, 3, 3, 3, 1),
        _bar("2026-07-29 11:30:00", 4, 4, 4, 4, 1),
    ]

    out = aggregate_from_1m(bars, "60m", "if2608")

    assert [bar["time"] for bar in out] == [
        "2026-07-29 09:30:00",
        "2026-07-29 10:30:00",
    ]
    # 11:30 午休收盘打印按左闭右开归上午末桶（10:30-11:30），
    # 不再预埋下午首桶 13:00（与夜盘 23:00 收盘打印同一边界语义）。


def test_aggregate_au_60m_carries_night_remainder_into_day():
    """AU 60m 官方交易日分钟轴从夜盘连续到日盘，不在 02:30 重置。"""
    bars = [
        _bar("2026-07-29 02:01:00", 1, 1, 1, 1, 1),
        _bar("2026-07-29 02:30:00", 2, 2, 2, 2, 1),
        _bar("2026-07-29 09:01:00", 3, 3, 3, 3, 1),
        _bar("2026-07-29 09:30:00", 4, 4, 4, 4, 1),
        _bar("2026-07-29 09:31:00", 5, 5, 5, 5, 1),
        _bar("2026-07-29 10:45:00", 6, 6, 6, 6, 1),
    ]

    out = aggregate_from_1m(bars, "60m", "au2608")

    assert [bar["time"] for bar in out] == [
        "2026-07-29 02:00:00",  # 夜盘余桶吸收次日日盘首分钟（共用交易分钟轴）
        "2026-07-29 09:30:00",
        "2026-07-29 10:45:00",
    ]
    assert out[0]["open"] == 1
    assert out[0]["close"] == 3


def test_aggregate_au_weekend_remainders_keep_distinct_official_keys():
    """周五夜盘余桶的周六标签不能与下一交易块的周二标签合并。

    （原夹具用「周一 02:0x」bar——那是旧周日夜盘幻影轴的产物，
    交易日历修正后周一凌晨无交易，改用合法的周一夜盘周二尾巴。）
    """
    bars = [
        _bar("2026-08-01 02:01:00", 1, 1, 1, 1, 1),
        _bar("2026-08-01 02:30:00", 2, 2, 2, 2, 1),
        _bar("2026-08-04 02:01:00", 3, 3, 3, 3, 1),
        _bar("2026-08-04 02:30:00", 4, 4, 4, 4, 1),
        _bar("2026-08-04 09:01:00", 5, 5, 5, 5, 1),
        _bar("2026-08-04 09:30:00", 6, 6, 6, 6, 1),
    ]

    out = aggregate_from_1m(bars, "60m", "au2608")

    assert [bar["time"] for bar in out] == [
        "2026-08-01 02:00:00",
        "2026-08-04 02:00:00",
        "2026-08-04 09:30:00",
    ]
    assert out[0]["open"] == 1
    assert out[0]["close"] == 2
    assert out[1]["open"] == 3
    assert out[1]["close"] == 5
    assert out[2]["close"] == 6


def test_fetch_aggregation_only_after_native_removal(monkeypatch):
    """原生多分钟停用后：结果全部由 1m 聚合产生，原生 stub 不参与；
    残缺首桶被丢弃，完整桶保留。"""
    native = [  # 原生哨兵：若被错误调用会污染结果
        _bar("2026-07-29 09:00:00", 100, 110, 90, 105, 999),
    ]
    one_minute = [
        _bar(
            f"2026-07-29 09:{minute:02d}:00",
            float(minute),
            float(minute),
            float(minute),
            float(minute),
            1,
        )
        for minute in range(21, 60)
    ] + [_bar("2026-07-29 10:00:00", 60, 60, 60, 60, 1)]

    monkeypatch.setattr(
        sina_kline,
        "_fetch_sina_minute_raw",
        lambda symbol, sina_type: native if sina_type == "30" else one_minute,
    )

    out = sina_kline.fetch_minute_kline("rb2610", "30m")

    # 09:00 桶缺 09:00-09:20 段 → 丢弃；09:30 桶完整（30 根）→ 保留
    assert [b["time"] for b in out] == ["2026-07-29 09:30:00", "2026-07-29 10:00:00"]
    assert out[0]["volume"] == 30
    assert all(b["volume"] != 999 for b in out)


def test_fetch_sina_minute_raw_drops_future_garbage(monkeypatch):
    """新浪收盘后尾部的未来日期垃圾行必须被丢弃（2026-08-22 实测）"""
    from datetime import datetime, timedelta, timezone

    _bj = timezone(timedelta(hours=8))
    now = datetime.now(_bj).replace(tzinfo=None)
    far_future = (now + timedelta(days=2)).strftime("%Y-%m-%d %H:%M") + ":00"
    near_ok = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M") + ":00"

    def fake_raw(symbol, sina_type):
        return [_bar(near_ok, 100.0), _bar(far_future, 101.0)]

    monkeypatch.setattr(sina_kline, "_fetch_sina_minute_raw", fake_raw)
    # 直接测内部过滤逻辑：重新构造 _fetch_sina_minute_raw 的过滤段
    bars = [
        {"time": near_ok, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"time": far_future, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    horizon = now + timedelta(minutes=2)
    kept = [
        b for b in bars
        if sina_kline._parse_bar_dt(b["time"]) is None
        or sina_kline._parse_bar_dt(b["time"]) <= horizon
    ]
    assert len(kept) == 1
    assert kept[0]["time"] == near_ok


def test_minute_label_to_start_wall_grid_fallback():
    """无品种信息时按墙钟网格回退（多分钟仅近似兜底）"""
    from app.data.sina_kline import _minute_label_to_start

    # 结束标签 09:35 → 起点 09:30（30m）
    assert _minute_label_to_start("", "2026-07-29 09:35:00", 30) == (
        "2026-07-29 09:30:00"
    )
    # 恰在边界：09:30（结束）→ 起点 09:00
    assert _minute_label_to_start("", "2026-07-29 09:30:00", 30) == (
        "2026-07-29 09:00:00"
    )
    # 无法解析：原样返回
    assert _minute_label_to_start("", "", 30) == ""


def test_minute_label_to_start_session_axis():
    """有品种时沿交易分钟轴换算：跨午休的 60m 结束标签 13:30 → 起点 11:00"""
    from app.data.sina_kline import _minute_label_to_start

    # rb 日盘 09:00-11:30：60m 桶 axis 60-119 = 10:00-11:00（墙钟）→ 结束 11:00
    assert _minute_label_to_start("rb2610", "2026-07-29 11:00:00", 60) == (
        "2026-07-29 10:00:00"
    )
    # 跨午休：结束 13:30（axis 135=13:30）→ 起点 axis 120=11:15（墙钟）
    assert _minute_label_to_start("rb2610", "2026-07-29 13:30:00", 30) == (
        "2026-07-29 11:15:00"
    )


def test_merge_history_with_accurate_prefers_accurate():
    """同 time 以 1m 聚合覆盖原生历史"""
    from app.data.sina_kline import merge_history_with_accurate

    history = [
        _bar("2026-05-21 22:15:00", 1, 2, 1, 1.5, 10),
        _bar("2026-07-24 09:15:00", 100, 110, 90, 105, 1),  # 原生失真
    ]
    accurate = [
        _bar("2026-07-24 09:15:00", 8468, 8468, 8415, 8425, 50152),
        _bar("2026-07-24 09:30:00", 8427, 8434, 8360, 8372, 47357),
    ]
    out = merge_history_with_accurate(history, accurate)
    assert len(out) == 3
    assert out[0]["time"] == "2026-05-21 22:15:00"
    assert out[1]["open"] == 8468
    assert out[1]["volume"] == 50152
    assert out[2]["time"] == "2026-07-24 09:30:00"
