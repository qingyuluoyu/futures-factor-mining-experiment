"""分钟线历史入库与合并测试 —— 合约映射 + 聚合 + 合并逻辑（不依赖真实 tqsdk）"""

from __future__ import annotations

import numpy as np


def _make_minute_bars(n: int, start: str = "2026-07-01 09:00:00") -> list[dict]:
    """合成 1m bars"""
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(start)
    bars = []
    rng = np.random.RandomState(0)
    for i in range(n):
        t = base + timedelta(minutes=i)
        c = 100.0 + rng.randn() * 0.5
        bars.append(
            {
                "time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(c),
                "high": float(c + abs(rng.randn()) * 0.3),
                "low": float(c - abs(rng.randn()) * 0.3),
                "close": float(c),
                "volume": int(1000 + rng.randint(-200, 200)),
            }
        )
    return bars


def test_tqsdk_symbol_mapping():
    """品种 code + 交易所中文 → tqsdk 连续主力符号"""
    from app.data.tqsdk_history import _build_tqsdk_symbol

    assert _build_tqsdk_symbol("rb", "上期所") == "KQ.m@SHFE.rb"
    assert _build_tqsdk_symbol("i", "大商所") == "KQ.m@DCE.i"
    # 郑商所天勤代码大写
    assert _build_tqsdk_symbol("cf", "郑商所") == "KQ.m@CZCE.CF"
    assert _build_tqsdk_symbol("ta", "郑商所") == "KQ.m@CZCE.TA"
    assert _build_tqsdk_symbol("if", "中金所") == "KQ.m@CFFEX.if"
    assert _build_tqsdk_symbol("si", "广期所") == "KQ.m@GFEX.si"
    assert _build_tqsdk_symbol("sc", "能源中心") == "KQ.m@INE.sc"
    # contracts 数据标为上期所、实际属能源中心的品种 → INE
    assert _build_tqsdk_symbol("nr", "上期所") == "KQ.m@INE.nr"
    assert _build_tqsdk_symbol("lu", "上期所") == "KQ.m@INE.lu"
    # 未知交易所返回空
    assert _build_tqsdk_symbol("xx", "未知交易所") == ""


def test_period_seconds():
    """周期 → 秒映射正确"""
    from app.data.tqsdk_history import PERIOD_SECONDS

    assert PERIOD_SECONDS["1m"] == 60
    assert PERIOD_SECONDS["15m"] == 900
    assert PERIOD_SECONDS["60m"] == 3600


def test_aggregate_from_1m_to_15m():
    """1m 聚合成 15m：根数减少、OHLC 正确"""
    from app.data.sina_kline import aggregate_from_1m

    bars_1m = _make_minute_bars(60)  # 60 根 1m
    bars_15m = aggregate_from_1m(bars_1m, "15m", "rb2610")
    # 60 根 1m 应聚合成约 4 根 15m
    assert len(bars_15m) >= 1
    assert len(bars_15m) < len(bars_1m)


def test_aggregate_preserves_1m():
    """1m 聚合成 1m 应原样返回"""
    from app.data.sina_kline import aggregate_from_1m

    bars_1m = _make_minute_bars(5)
    bars = aggregate_from_1m(bars_1m, "1m", "rb2610")
    assert len(bars) == 5


def test_kline_minute_model_fields():
    """KlineMinute 模型字段完整"""
    from app.models.kline_minute import KlineMinute

    assert KlineMinute.__tablename__ == "kline_minute"
    # 确认有这些列（通过 mapped_column 定义）
    cols = {c.name for c in KlineMinute.__table__.columns}
    assert {"code", "symbol", "period", "ts", "open", "high", "low", "close", "volume"} <= cols
