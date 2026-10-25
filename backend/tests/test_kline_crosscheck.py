"""K线多源交叉验证测试 —— 一致性判定 + 仲裁 + 单源兜底（无网络）

tqsdk/eastmoney 已移除（2026-08-22）：默认链仅剩新浪单源，交叉验证退化为
存在性校验；多源一致性判定逻辑保留（供自定义 sources 使用）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.data import kline_crosscheck as xc


def _bar(
    time: str = "2026-08-10",
    open_: float = 3000,
    high: float = 3010,
    low: float = 2990,
    close: float = 3005,
    volume: int = 100000,
) -> dict[str, Any]:
    return {
        "time": time,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "settle": None,
        "open_interest": None,
    }


# ---------- bars_consistent ----------


def test_bars_consistent_exact():
    """完全相等 → True"""
    b = _bar()
    assert xc.bars_consistent(b, _bar(), 1.0) is True


def test_bars_consistent_within_tolerance():
    """差 1 跳 → True（rb tolerance=1）"""
    b1 = _bar(open_=3000, close=3005)
    b2 = _bar(open_=3001, close=3005)
    assert xc.bars_consistent(b1, b2, 1.0) is True


def test_bars_consistent_at_boundary():
    """差正好等于 tolerance → True（边界值）"""
    b1 = _bar(open_=3000)
    b2 = _bar(open_=3001)
    assert xc.bars_consistent(b1, b2, 1.0) is True


def test_bars_consistent_beyond_tolerance():
    """差 2 跳 → False"""
    b1 = _bar(open_=3000)
    b2 = _bar(open_=3002)
    assert xc.bars_consistent(b1, b2, 1.0) is False


def test_bars_consistent_au_small_tick():
    """沪金 tolerance=0.02，差 0.02 → True"""
    b1 = _bar(open_=500.00, close=501.00)
    b2 = _bar(open_=500.02, close=501.00)
    assert xc.bars_consistent(b1, b2, 0.02) is True


def test_bars_consistent_volume_ignored():
    """volume 不同不影响一致性"""
    b1 = _bar(volume=100000)
    b2 = _bar(volume=200000)
    assert xc.bars_consistent(b1, b2, 1.0) is True


# ---------- _pick_consensus ----------


def test_consensus_sources_agree():
    """多源一致 → verified"""
    bars = {
        "sina": _bar(close=3005),
        "simnow": _bar(close=3005),
    }
    consensus, verdict = xc._pick_consensus(bars, 1.0)
    assert verdict == "verified"
    assert consensus is not None
    assert consensus["close"] == 3005


def test_consensus_all_conflict_arbitrates():
    """所有源都不同 → conflict，按仲裁顺序取第一个"""
    bars = {
        "sina": _bar(close=3005),
        "simnow": _bar(close=3008),
    }
    consensus, verdict = xc._pick_consensus(bars, 1.0)
    assert verdict == "conflict"
    assert consensus["close"] == 3005  # 兜底：取列表中第一个源


def test_consensus_single_source():
    """仅 1 源 → single"""
    bars = {"sina": _bar(close=3005)}
    consensus, verdict = xc._pick_consensus(bars, 1.0)
    assert verdict == "single"
    assert consensus["close"] == 3005


# ---------- tolerance_for ----------


def test_tolerance_rb():
    """螺纹钢 tolerance = 1（tick_size=1 × 1 跳）"""
    assert xc._tolerance_for("RB") == 1.0


def test_tolerance_au():
    """沪金 tolerance = 0.02"""
    assert xc._tolerance_for("AU") == 0.02


def test_tolerance_cu():
    """沪铜 tolerance = 10"""
    assert xc._tolerance_for("CU") == 10.0


# ---------- cross_validate_and_merge（mock fetchers）----------


@pytest.mark.asyncio
async def test_cross_validate_single_source(monkeypatch: pytest.MonkeyPatch):
    """默认链仅新浪单源 → single，返回其数据"""
    bars = [_bar(time="2026-08-07"), _bar(time="2026-08-08")]
    monkeypatch.setattr("app.data.kline_provider._fetch_sina", lambda s, p: list(bars))

    merged, stats = await xc.cross_validate_and_merge("rb2610", "1d")
    assert stats["single"] == 2
    assert len(merged) == 2


@pytest.mark.asyncio
async def test_cross_validate_explicit_sources_agree(monkeypatch: pytest.MonkeyPatch):
    """显式传多源一致 → verified"""
    bars = [_bar(time="2026-08-07", close=3005)]
    monkeypatch.setattr("app.data.kline_provider._fetch_sina", lambda s, p: list(bars))
    monkeypatch.setattr("app.data.kline_provider._fetch_simnow", lambda s, p: list(bars))

    merged, stats = await xc.cross_validate_and_merge(
        "rb2610", "1d", sources=["sina", "simnow"]
    )
    assert stats["verified"] == 1
    assert merged[0]["close"] == 3005


@pytest.mark.asyncio
async def test_cross_validate_all_fail(monkeypatch: pytest.MonkeyPatch):
    """所有源都无数据 → 返回空"""
    monkeypatch.setattr("app.data.kline_provider._fetch_sina", lambda s, p: [])

    merged, stats = await xc.cross_validate_and_merge("rb2610", "1d")
    assert merged == []
    assert stats["verified"] == 0
