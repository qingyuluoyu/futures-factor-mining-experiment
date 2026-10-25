"""分钟历史边界提示测试 —— 取数为空时报错说明实际可回测起点"""

from __future__ import annotations

from app.services.backtest.data import _no_data_hint


async def test_minute_hint_includes_boundary(monkeypatch):
    """tickdata 分钟最早 2025-10-09 → 文案带边界与日线建议"""
    from app.data import tickdata_kline as td

    async def _fake_earliest(product, period):
        assert product == "ma"
        assert period == "15m"
        return "2025-10-09"

    monkeypatch.setattr(td, "earliest_minute_date", _fake_earliest)
    msg = await _no_data_hint("ma2609", "15m")
    assert "2025-10-09" in msg
    assert "日线" in msg


async def test_minute_hint_falls_back_when_no_boundary(monkeypatch):
    """查不到边界（未配置/无数据）→ 原通用文案"""
    from app.data import tickdata_kline as td

    async def _fake_earliest(product, period):
        return None

    monkeypatch.setattr(td, "earliest_minute_date", _fake_earliest)
    msg = await _no_data_hint("ma2609", "15m")
    assert msg.startswith("未获取到 K 线数据")


async def test_daily_hint_is_generic():
    """日线不走边界提示（tickdata 日线历史深，2005 起）"""
    msg = await _no_data_hint("ma2609", "1d")
    assert msg.startswith("未获取到 K 线数据")


async def test_unknown_symbol_is_safe():
    """无法识别品种也不抛异常"""
    msg = await _no_data_hint("???", "15m")
    assert isinstance(msg, str)
