"""多段回测：随机抽段、区间校验、schema 约束与聚合报告"""

import random
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.backtest import BacktestRunRequest
from app.services.backtest import runner as runner_mod
from app.services.backtest.data import (
    pick_random_segments,
    validate_range,
)
from app.services.backtest.metrics import multi_segment_summary
from app.services.backtest.runner import run_backtest


# ----------------------------------------------------------------
# pick_random_segments 纯函数
# ----------------------------------------------------------------


def test_pick_random_segments_tiles_when_no_slack():
    """富余=0（恰好最小区间）时，段应首尾相接铺满整个区间"""
    start = date(2026, 1, 1)
    end = start + timedelta(days=89)  # 含首尾 90 天 = 3×30
    segs = pick_random_segments(start, end, 3, 30, rng=random.Random(42))

    assert len(segs) == 3
    assert segs[0][0] == start
    assert segs[-1][1] == end
    for i, (s, e) in enumerate(segs):
        assert (e - s).days + 1 == 30, f"段{i} 长度应为 30 天"
    for i in range(2):
        assert segs[i][1] + timedelta(days=1) == segs[i + 1][0], "段间应无缝衔接"


def test_pick_random_segments_invariants_with_slack():
    """有富余时：段长恒等、互不重叠、按时间排序、都在区间内"""
    start = date(2025, 6, 1)
    end = start + timedelta(days=199)  # 200 天，5 段×7 天 → 富余 165
    segs = pick_random_segments(start, end, 5, 7, rng=random.Random(7))

    assert len(segs) == 5
    for i, (s, e) in enumerate(segs):
        assert (e - s).days + 1 == 7, f"段{i} 长度应为 7 天"
        assert start <= s <= e <= end
    for i in range(4):
        assert segs[i][1] < segs[i + 1][0], "段与段不应重叠"


def test_pick_random_segments_raises_when_too_short():
    start = date(2026, 1, 1)
    end = start + timedelta(days=59)  # 60 天 < 3×30
    with pytest.raises(ValueError):
        pick_random_segments(start, end, 3, 30, rng=random.Random(1))


def test_pick_random_segments_randomness_varies():
    """不同种子应产生不同分布（抽查，避免退化成固定切分）"""
    start = date(2025, 1, 1)
    end = start + timedelta(days=99)
    a = pick_random_segments(start, end, 2, 30, rng=random.Random(1))
    b = pick_random_segments(start, end, 2, 30, rng=random.Random(2))
    assert a != b


# ----------------------------------------------------------------
# validate_range 多段模式
# ----------------------------------------------------------------


def test_validate_range_multi_min_days():
    """15m 3 段：89 天（含首尾）不足，90 天恰好通过，超长无上限"""
    start = date(2026, 1, 1)
    with pytest.raises(ValueError, match="至少 90 天"):
        validate_range(start, start + timedelta(days=88), "15m", segment_count=3)
    # 恰好 90 天（含首尾）通过
    validate_range(start, start + timedelta(days=89), "15m", segment_count=3)
    # 多段模式无上限：400 天也通过
    validate_range(start, start + timedelta(days=399), "15m", segment_count=3)


def test_validate_range_single_mode_unaffected():
    """单段模式维持原上限：15m 400 天报错，30 天内通过"""
    start = date(2026, 1, 1)
    with pytest.raises(ValueError, match="最多 30 天"):
        validate_range(start, start + timedelta(days=399), "15m")
    validate_range(start, start + timedelta(days=30), "15m")


def test_validate_range_segment_count_bounds():
    start = date(2025, 1, 1)
    end = start + timedelta(days=400)
    with pytest.raises(ValueError, match="段数"):
        validate_range(start, end, "1m", segment_count=1)
    with pytest.raises(ValueError, match="段数"):
        validate_range(start, end, "1m", segment_count=6)


# ----------------------------------------------------------------
# schema 约束
# ----------------------------------------------------------------


def _base_payload(**overrides) -> dict:
    payload = {
        "strategy_type": "n_breakout",
        "symbol": "pp",
        "timeframe": "15m",
        "start_date": "2026-05-01",
        "end_date": "2026-08-10",
    }
    payload.update(overrides)
    return payload


def test_schema_rejects_daily_multi_segment():
    with pytest.raises(ValidationError, match="日线不支持多段回测"):
        BacktestRunRequest(**_base_payload(timeframe="1d", multi_segment=True))


def test_schema_segment_count_bounds():
    with pytest.raises(ValidationError):
        BacktestRunRequest(**_base_payload(multi_segment=True, segment_count=1))
    with pytest.raises(ValidationError):
        BacktestRunRequest(**_base_payload(multi_segment=True, segment_count=6))
    req = BacktestRunRequest(**_base_payload(multi_segment=True, segment_count=5))
    assert req.segment_count == 5


def test_schema_multi_segment_defaults_off():
    req = BacktestRunRequest(**_base_payload())
    assert req.multi_segment is False
    assert req.segment_count == 2


def test_schema_accepts_all_registered_strategies():
    """回归：strategy_type 白名单派生自 QUANT_STRATEGY_TYPES——曾硬编码漏
    strength_entry / strength_entry_v2，注册表新策略必须能发起回测"""
    from app.services.ai_trading.strategies import QUANT_STRATEGY_TYPES

    for kind in QUANT_STRATEGY_TYPES:
        req = BacktestRunRequest(**_base_payload(strategy_type=kind))
        assert req.strategy_type == kind
    # ai 独立于量化注册表，同样放行；未知值拒绝
    assert BacktestRunRequest(**_base_payload(strategy_type="ai")).strategy_type == "ai"
    with pytest.raises(ValidationError, match="strategy_type 须为"):
        BacktestRunRequest(**_base_payload(strategy_type="no_such_strategy"))


# ----------------------------------------------------------------
# 多段汇总指标
# ----------------------------------------------------------------


def test_multi_segment_summary_math():
    segs = [
        {"total_return_pct": 10.0, "max_drawdown_pct": 5.0},
        {"total_return_pct": -5.0, "max_drawdown_pct": 3.0},
    ]
    s = multi_segment_summary(segs)
    assert s["segment_count"] == 2
    assert s["winning_segments"] == 1
    assert s["segment_win_rate"] == 50.0
    assert s["mean_segment_return_pct"] == 2.5
    # 复合：1.10 × 0.95 = 1.045 → +4.5%
    assert s["compound_return_pct"] == pytest.approx(4.5, abs=1e-3)
    assert s["mean_segment_drawdown_pct"] == 4.0


# ----------------------------------------------------------------
# run_backtest 多段集成（fake bars + monkeypatch，仿 test_backtest_runner）
# ----------------------------------------------------------------


def _bar(time: str, o: float, h: float, low: float, c: float, vol: int = 1000) -> dict:
    return {
        "time": time,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": vol,
        "settle": c,
        "open_interest": 0,
    }


def _fake_trade_params(symbol: str, settings) -> dict:
    return {
        "multiplier": 10,
        "margin_rate": 0.1,
        "fee_mode": "fixed",
        "open_fee": 5.0,
        "close_fee": 5.0,
    }


def _trend_bars(start: date, days: int) -> list[dict]:
    """温和上升趋势日线序列（时间粒度不影响多段结构断言）"""
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        close = 100 + 0.4 * i + (i % 7) * 0.3
        out.append(_bar(d.isoformat(), close - 0.5, close + 1, close - 1, close))
    return out


_FIXED_SEGMENTS = [
    (date(2026, 5, 5), date(2026, 6, 3)),
    (date(2026, 6, 10), date(2026, 7, 9)),
    (date(2026, 7, 15), date(2026, 8, 13)),
]


@pytest.mark.asyncio
async def test_run_backtest_multi_segment_report(monkeypatch):
    """多段回测：逐段独立执行 + 聚合字段 + trades/decisions 段标签"""
    load_calls: list[tuple[date, date]] = []

    async def fake_load(redis_conn, session, symbol, timeframe, start, end):
        load_calls.append((start, end))
        return _trend_bars(date(2026, 4, 1), 140)

    monkeypatch.setattr(runner_mod, "load_backtest_bars", fake_load)
    monkeypatch.setattr(runner_mod, "resolve_trade_params", _fake_trade_params)
    monkeypatch.setattr(
        runner_mod,
        "pick_random_segments",
        lambda start, end, n, seg_days, rng=None: _FIXED_SEGMENTS[:n],
    )

    payload = {
        "symbol": "pp",
        "timeframe": "15m",
        "start_date": "2026-05-01",
        "end_date": "2026-08-10",  # 含首尾 102 天 ≥ 3×30
        "strategy_type": "n_breakout",
        "strategy_params": {"lookback": 5},
        "side_mode": "long_only",
        "fixed_qty": 1,
        "initial_cash": 1_000_000,
        "multi_segment": True,
        "segment_count": 3,
    }

    result = await run_backtest(session=None, redis_conn=None, user_id=None, payload=payload)

    # 每段独立加载，按固定段顺序
    assert load_calls == _FIXED_SEGMENTS

    cfg = result["config"]
    assert cfg["multi_segment"] is True
    assert cfg["segment_count"] == 3
    assert cfg["segment_ranges"] == [[s.isoformat(), e.isoformat()] for s, e in _FIXED_SEGMENTS]

    segs = result["segments"]
    assert len(segs) == 3
    for idx, (sr, (s, e)) in enumerate(zip(segs, _FIXED_SEGMENTS)):
        assert sr["index"] == idx
        assert sr["start_date"] == s.isoformat()
        assert sr["end_date"] == e.isoformat()
        for key in ("metrics", "equity_curve", "bars", "trades", "decisions", "message"):
            assert key in sr, f"段{idx}缺少 {key}"

    # 聚合指标包含段级汇总字段
    for key in (
        "segment_count",
        "winning_segments",
        "segment_win_rate",
        "mean_segment_return_pct",
        "compound_return_pct",
    ):
        assert key in result["metrics"]

    # 合并的 trades/decisions 带 segment 标签；多段不输出拼接 K 线
    assert result["bars"] == []
    for t in result["trades"]:
        assert t.get("segment") in (0, 1, 2)
    for d in result["decisions"]:
        assert d.get("segment") in (0, 1, 2)

    assert "3 段随机回测完成" in result["message"]


@pytest.mark.asyncio
async def test_run_backtest_multi_segment_too_short_range(monkeypatch):
    """区间不足最小区间时，多段回测在入口即报错"""
    monkeypatch.setattr(runner_mod, "resolve_trade_params", _fake_trade_params)

    payload = {
        "symbol": "pp",
        "timeframe": "15m",
        "start_date": "2026-07-01",
        "end_date": "2026-08-10",  # 含首尾 41 天 < 90
        "strategy_type": "n_breakout",
        "multi_segment": True,
        "segment_count": 3,
    }
    with pytest.raises(ValueError, match="至少 90 天"):
        await run_backtest(session=None, redis_conn=None, user_id=None, payload=payload)


@pytest.mark.asyncio
async def test_run_backtest_single_mode_has_no_segments(monkeypatch):
    """单段路径响应结构与旧版一致：无 segments、config.multi_segment=False"""

    async def fake_load(*args, **kwargs):
        return _trend_bars(date(2026, 6, 1), 60)

    monkeypatch.setattr(runner_mod, "load_backtest_bars", fake_load)
    monkeypatch.setattr(runner_mod, "resolve_trade_params", _fake_trade_params)

    payload = {
        "symbol": "pp",
        "timeframe": "1d",
        "start_date": "2026-06-10",
        "end_date": "2026-07-30",
        "strategy_type": "n_breakout",
        "strategy_params": {"lookback": 5},
        "side_mode": "long_only",
    }
    result = await run_backtest(session=None, redis_conn=None, user_id=None, payload=payload)

    assert "segments" not in result
    assert result["config"]["multi_segment"] is False
    assert result["bars"], "单段模式仍应输出图表 K 线"
    assert "回测完成" in result["message"]
