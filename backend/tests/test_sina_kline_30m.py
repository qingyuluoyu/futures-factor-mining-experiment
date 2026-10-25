"""30m/60m K 线用交易时段感知的 1m 聚合覆盖近期

交易时段感知聚合会跳过商品日盘 10:15~10:30 和午休，时间键与原生行情一致；
近期 OHLCV 以 1m 聚合为准，原生多分钟只负责补更早历史。
"""

from __future__ import annotations

from unittest.mock import patch

from app.data.sina_kline import fetch_minute_kline


def _bar(t: str, c: float = 100.0) -> dict:
    return {
        "time": t,
        "open": c,
        "high": c,
        "low": c,
        "close": c,
        "volume": 10,
        "settle": None,
        "open_interest": 100,
    }


def _fetch_stub(sina_type: str):
    """构造一个按 sina_type 返回不同数据的假 _fetch_sina_minute_raw

    stub 位于取数层之后：返回的 bar 均已是「起点」标签（真实路径中
    _fetch_sina_minute_raw 已把新浪结束标签换算为起点）。
    """

    def _impl(symbol: str, st: str):
        if st == "1":
            # 1m 数据（起点标签）：10:00~10:15 连续，缺 10:16~10:30（休盘），
            # 10:31~10:45 连续
            return [
                _bar("2026-07-29 10:00:00", 101),
                _bar("2026-07-29 10:15:00", 102),
                _bar("2026-07-29 10:31:00", 103),
                _bar("2026-07-29 10:45:00", 104),
                _bar("2026-07-29 11:15:00", 105),
            ]
        if st == "30":
            # 原生多分钟已停用：不应被调用，返回哨兵以便断言
            return [_bar("2026-07-29 10:00:00", 9999)]
        if st == "60":
            return [_bar("2026-07-29 10:00:00", 9999)]
        if st == "15":
            return [_bar("2026-07-29 10:00:00"), _bar("2026-07-29 10:15:00"), _bar("2026-07-29 10:45:00")]
        if st == "5":
            return [_bar("2026-07-29 10:00:00"), _bar("2026-07-29 10:05:00")]
        return []

    return _impl


def test_30m_uses_session_aware_1m_aggregation() -> None:
    """30m 时间键不漂移；原生多分钟已停用，全部由 1m 聚合产生。"""
    with patch(
        "app.data.sina_kline._fetch_sina_minute_raw", side_effect=_fetch_stub("30")
    ):
        bars = fetch_minute_kline("ma2609", "30m")

    times = [b["time"] for b in bars]
    # ma（郑商所）10:15-10:30 小休：axis 60-89 桶 = 10:00-10:15 + 10:30-10:45
    assert times == ["2026-07-29 10:00:00", "2026-07-29 10:45:00", "2026-07-29 11:15:00"]
    assert bars[0]["open"] == 101
    assert bars[0]["close"] == 103
    assert bars[1]["open"] == 104
    assert bars[1]["close"] == 104
    assert bars[2]["open"] == 105
    # 原生哨兵(9999)不得出现——原生接口不应参与供数
    assert all(b["open"] != 9999 for b in bars)


def test_60m_uses_session_aware_1m_aggregation() -> None:
    """60m 跨小休落桶后，同键价格由 1m 聚合覆盖；原生不参与。"""
    with patch(
        "app.data.sina_kline._fetch_sina_minute_raw", side_effect=_fetch_stub("60")
    ):
        bars = fetch_minute_kline("ma2609", "60m")

    times = [b["time"] for b in bars]
    assert times == ["2026-07-29 10:00:00", "2026-07-29 11:15:00"]
    # 10:00 桶（axis 60-119：10:00-10:15 + 10:30-11:14）由 1m 聚合覆盖
    assert bars[0]["open"] == 101
    assert bars[0]["close"] == 104
    # 11:15 桶（axis 120 起的新桶）亦有 1m 覆盖
    assert bars[-1]["open"] == 105
    assert all(b["open"] != 9999 for b in bars)


def test_5m_still_merges_aggregation() -> None:
    """5m 仍走 1m 聚合合并（回归保护：5m/15m 不受本修复影响）"""
    with patch(
        "app.data.sina_kline._fetch_sina_minute_raw", side_effect=_fetch_stub("5")
    ):
        bars = fetch_minute_kline("ma2609", "5m")

    times = [b["time"] for b in bars]
    # 5m 应包含原生 + 聚合的 bar（合并后）
    assert len(times) >= 2


def test_30m_falls_back_to_aggregation_when_native_fails() -> None:
    """新浪原生 30m 拉取失败时，降级用 1m 聚合兜底（不返回空）"""

    def _impl(symbol: str, st: str):
        if st == "30":
            raise RuntimeError("新浪原生拉取失败")
        if st == "1":
            return [_bar("2026-07-29 10:00:00"), _bar("2026-07-29 10:15:00")]
        return []

    with patch("app.data.sina_kline._fetch_sina_minute_raw", side_effect=_impl):
        bars = fetch_minute_kline("ma2609", "30m")

    # 原生失败时降级 1m 聚合，仍返回数据（不空）
    assert isinstance(bars, list)
    assert len(bars) > 0


def test_30m_empty_when_1m_fails() -> None:
    """1m 拉取失败且原生停用 → 返回空（由调用方降级 simnow/remote）。"""

    def _impl(symbol: str, st: str):
        if st == "1":
            raise RuntimeError("1m 拉取失败")
        if st == "30":
            return [_bar("2026-07-29 09:30:00", 900)]
        return []

    with patch("app.data.sina_kline._fetch_sina_minute_raw", side_effect=_impl):
        bars = fetch_minute_kline("ma2609", "30m")

    assert bars == []
