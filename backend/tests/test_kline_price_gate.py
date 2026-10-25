"""K 线最新视图价格一致性门（跨源脏价防护）"""

from __future__ import annotations

from datetime import datetime

from app.services.kline import _build_price_gate


def _bars(close: float, time_str: str = "2026-08-22 02:15:00") -> list[dict]:
    return [{"time": time_str, "open": close, "high": close,
             "low": close, "close": close, "volume": 1}]


def test_dirty_source_rejected():
    """au2612 实测场景：新浪 979 vs 基准 1004（偏离 2.5%）→ 拒用"""
    gate = _build_price_gate(1004.1, datetime(2026, 8, 22, 2, 25), 15)
    assert gate(_bars(979.16, "2026-08-22 02:15:00")) is True


def test_normal_source_passed():
    gate = _build_price_gate(1004.1, datetime(2026, 8, 22, 2, 25), 15)
    assert gate(_bars(1003.5, "2026-08-22 02:15:00")) is False
    # 1% 内的口径差放行
    assert gate(_bars(995.0, "2026-08-22 02:15:00")) is False


def test_stale_reference_skipped():
    """基准远新于候选 bar（>3×周期）：偏差来自行情推进，不拦"""
    gate = _build_price_gate(1004.1, datetime(2026, 8, 22, 10, 0), 15)
    # bar 02:15，基准 10:00：领先 7h45m ≫ 45min → 放行
    assert gate(_bars(979.16, "2026-08-22 02:15:00")) is False


def test_null_reference_never_rejects():
    gate = _build_price_gate(0.0, None, 15)
    assert gate(_bars(1.0)) is False


def test_bad_close_never_rejects():
    gate = _build_price_gate(1004.1, None, 15)
    assert gate([{"time": "t", "close": None}]) is False
    assert gate([]) is False
