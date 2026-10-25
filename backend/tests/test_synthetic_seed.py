"""AI 合成 K 线 · 真实历史种子（微观结构校准）单测

覆盖：统计标定（profile）、种子化渲染不变量（波动率聚集/跳空/精度）、
CSV 目录与 provider 两种历史接入方式、generate_synthetic_series 集成
（接种子 + 未接入回退）。
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.services.backtest import synthetic_seed
from app.services.backtest.synthetic import (
    MarketScript,
    _default_script,
    generate_synthetic_series,
    render_bars_from_script,
)
from app.services.backtest.synthetic_seed import (
    MIN_SEED_BARS,
    HistorySeedProfile,
    build_seed_profile,
    is_session_boundary,
    load_seed_bars_from_csv,
    pick_random_window,
    resolve_seed_segment,
    summarize_seed_for_prompt,
)

# ------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------

def _mk_daily_bars(n: int = 300, *, gap_every: int = 5, seed: int = 7) -> list[dict[str, Any]]:
    """日线种子：每 gap_every 根一个开盘跳空，价格 2 位小数"""
    rng = random.Random(seed)
    bars: list[dict[str, Any]] = []
    d = date(2023, 3, 1)
    price = 3600.0
    for i in range(n):
        if i % gap_every == 0 and i:
            o = price * (1 + rng.uniform(-0.004, 0.004))
        else:
            o = price
        c = o * (1 + rng.gauss(0.0002, 0.012))
        hi = max(o, c) * (1 + abs(rng.gauss(0, 0.003)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.003)))
        vol = int(4000 * rng.uniform(0.6, 1.8))
        bars.append({
            "time": d.isoformat(),
            "open": round(o, 2), "high": round(hi, 2),
            "low": round(lo, 2), "close": round(c, 2), "volume": vol,
        })
        price = c
        d += timedelta(days=1 if d.weekday() < 4 else 3)
    return bars


def _mk_minute_bars(days: int = 10, *, seed: int = 11) -> list[dict[str, Any]]:
    """5 分钟种子：跨日与午休边界，边界根开盘跳空"""
    rng = random.Random(seed)
    slots: list[str] = []
    for h, m in [(9, 0), (9, 5), (9, 10), (9, 15)]:
        slots.append(f"{h:02d}:{m:02d}:00")
    slots += ["10:30:00", "10:35:00", "11:25:00", "11:30:00"]
    slots += ["13:30:00", "13:35:00", "14:55:00", "15:00:00"]
    bars: list[dict[str, Any]] = []
    d = date(2023, 6, 1)
    price = 7500.0
    for _ in range(days):
        for s in slots:
            boundary = s in ("09:00:00", "13:30:00")
            o = price * (1 + rng.uniform(-0.003, 0.003)) if boundary else price
            c = o * (1 + rng.gauss(0, 0.004))
            hi = max(o, c) * (1 + abs(rng.gauss(0, 0.0015)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0015)))
            bars.append({
                "time": f"{d.isoformat()} {s}",
                "open": round(o, 2), "high": round(hi, 2),
                "low": round(lo, 2), "close": round(c, 2),
                "volume": int(2000 * rng.uniform(0.5, 2.0)),
            })
            price = c
        d += timedelta(days=1)
    return bars


def _mk_profile(**overrides: Any) -> HistorySeedProfile:
    """渲染测试用直造 profile（不经统计），字段可覆写"""
    base = HistorySeedProfile(
        bars=_mk_daily_bars(120),
        symbol="rb", timeframe="1d",
        start="2023-03-01", end="2023-06-30",
        ref_price=3600.0, amplitude=0.02, bar_sigma=0.012,
        vol_persistence=0.8, vol_of_vol=0.3,
        gap_mean=0.0005, gap_std=0.004, gap_rate=0.85,
        vol_median=4000.0, vol_log_sigma=0.4, vol_edge_factor=1.6,
        wick_per_body_med=0.3, wick_per_body_hi=0.6,
        range_frac_p99=0.06, decimals=2,
    )
    return replace(base, **overrides)


def _write_csv(path, bars) -> None:
    import csv as _csv

    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = _csv.DictWriter(fp, fieldnames=["time", "open", "high", "low", "close", "volume"])
        w.writeheader()
        for b in bars:
            w.writerow({k: b[k] for k in w.fieldnames})


# ------------------------------------------------------------------
# 统计标定
# ------------------------------------------------------------------

def test_profile_stats_sane():
    p = build_seed_profile(_mk_daily_bars(300, gap_every=5), "1d", symbol="rb")
    assert p is not None
    assert 0.001 < p.amplitude < 0.2
    assert p.bar_sigma > 0
    assert 0.0 <= p.vol_persistence <= 0.95
    assert 0.05 <= p.vol_of_vol <= 0.9
    assert 0.0 < p.gap_rate <= 1.0  # 每 5 根一个跳空 → gap_rate>0
    assert p.gap_std > 0
    assert p.vol_median > 0
    assert 0.1 <= p.vol_log_sigma <= 1.0
    assert p.vol_edge_factor >= 1.0
    assert 0.05 <= p.wick_per_body_med < p.wick_per_body_hi
    assert p.range_frac_p99 > p.amplitude
    assert 0 <= p.decimals <= 2
    assert p.ref_price == pytest.approx(_mk_daily_bars(300, gap_every=5)[-1]["close"])
    assert p.start and p.end


def test_profile_minute_lunch_boundary_counted():
    """分钟种子：跨日 + 午休边界都算跳空边界（gap_rate 覆盖两类）"""
    bars = _mk_minute_bars(days=10)  # 10 天 × 12 根 = 120 根 ≥ 下限
    p = build_seed_profile(bars, "5m", symbol="pp")
    assert p is not None
    assert len(p.bars) == len(bars)
    # 每天两个边界根（09:00 跨日 / 13:30 午休）都强制跳空
    assert p.gap_rate > 0.8


def test_profile_insufficient_bars_returns_none():
    assert build_seed_profile(_mk_daily_bars(MIN_SEED_BARS - 1), "1d") is None
    assert build_seed_profile([], "1d") is None


def test_profile_invalid_bars_filtered():
    bars = _mk_daily_bars(100)
    for b in bars[:30]:
        b["close"] = 0  # 非法价格
    assert build_seed_profile(bars, "1d") is None  # 剩 70 < 80


def test_session_boundary_detection():
    from datetime import datetime
    a = datetime(2023, 6, 1, 11, 30)
    b = datetime(2023, 6, 1, 13, 30)
    c = datetime(2023, 6, 2, 9, 0)
    d = datetime(2023, 6, 1, 10, 35)
    assert is_session_boundary(a, b, "5m")       # 午休
    assert is_session_boundary(a, c, "5m")       # 跨日
    assert not is_session_boundary(d, a, "5m")   # 日内连续
    assert is_session_boundary(a, b, "1d")       # 日线相邻即隔夜


def test_summarize_seed_for_prompt_contains_stats_and_tail():
    p = build_seed_profile(_mk_daily_bars(200), "1d", symbol="rb")
    assert p is not None
    s = summarize_seed_for_prompt(p)
    assert "真实历史种子" in s
    assert "禁止复述" in s
    assert "O" in s and "V" in s  # 尾部 K 线行
    assert str(p.bars[-1]["close"]).split(".")[0] in s


# ------------------------------------------------------------------
# 种子化渲染
# ------------------------------------------------------------------

@pytest.mark.parametrize("timeframe", ["1d", "15m", "5m"])
def test_render_seeded_ohlc_invariants(timeframe: str):
    """种子化渲染仍满足 OHLC 合法性 + 价格贴合种子小数位"""
    p = _mk_profile(timeframe=timeframe, decimals=2)
    script = MarketScript(regime="volatile_range", trend_strength=0.5,
                          volatility=0.02, jumps=[{"at": 0.5, "direction": "up", "size": 0.03}])
    bars = render_bars_from_script(
        script, symbol="rb", timeframe=timeframe, num_bars=80,
        ref_price=p.ref_price, amplitude=p.amplitude, seed=42, seed_profile=p,
    )
    assert len(bars) == 80
    prev_close = p.ref_price
    for b in bars:
        assert b["low"] <= b["open"] <= b["high"], b
        assert b["low"] <= b["close"] <= b["high"], b
        assert b["open"] > 0 and b["close"] > 0 and b["volume"] > 0
        assert 0.85 * prev_close <= b["close"] <= 1.15 * prev_close, b
        for k in ("open", "high", "low", "close"):
            assert b[k] == round(b[k], 2)  # decimals=2
        prev_close = b["close"]


def test_render_seeded_gap_opens_at_boundaries():
    """gap_rate=1 + 明显 gap 分布 → 时段边界开盘价必跳开（≠昨收）"""
    p = _mk_profile(timeframe="15m", gap_rate=1.0, gap_mean=0.008, gap_std=0.006, decimals=2)
    bars = render_bars_from_script(
        MarketScript(regime="sideways", volatility=0.008), symbol="rb",
        timeframe="15m", num_bars=120, ref_price=3600.0,
        amplitude=0.008, seed=7, seed_profile=p,
    )
    tick = 0.01
    gap_cnt = 0
    for i in range(1, len(bars)):
        d_cur = bars[i]["time"][:10]
        d_prev = bars[i - 1]["time"][:10]
        t_cur = bars[i]["time"][11:16]
        overnight = d_cur != d_prev
        lunch = d_cur == d_prev and t_cur == "13:30"
        if overnight or lunch:
            assert abs(bars[i]["open"] - bars[i - 1]["close"]) > tick, bars[i]
            gap_cnt += 1
    assert gap_cnt >= 4  # 120 根 15m 至少跨 3 天 + 午休边界


def test_render_seeded_no_gap_when_rate_zero():
    """gap_rate=0 → 全部开盘价=昨收（旧路径行为）"""
    p = _mk_profile(timeframe="1d", gap_rate=0.0)
    bars = render_bars_from_script(
        MarketScript(regime="sideways", volatility=0.012), symbol="rb",
        timeframe="1d", num_bars=60, ref_price=3600.0,
        amplitude=0.012, seed=3, seed_profile=p,
    )
    for i in range(1, len(bars)):
        assert bars[i]["open"] == pytest.approx(bars[i - 1]["close"], abs=1e-9)


def test_render_vol_clustering_effect():
    """高持续性/vol-of-vol 的种子 → |收益| 离散度显著大于近常数波动"""
    script = MarketScript(regime="sideways", volatility=0.012)

    def _ret_spread(p: HistorySeedProfile) -> float:
        bars = render_bars_from_script(
            script, symbol="rb", timeframe="1d", num_bars=400,
            ref_price=3600.0, amplitude=0.012, seed=99, seed_profile=p,
        )
        rets = [abs(math.log(bars[i]["close"] / bars[i - 1]["close"])) for i in range(1, len(bars))]
        return max(rets) / max(statistics.median(rets), 1e-12)

    clustered = _mk_profile(vol_persistence=0.95, vol_of_vol=0.9)
    flat = _mk_profile(vol_persistence=0.0, vol_of_vol=0.05)
    assert _ret_spread(clustered) > _ret_spread(flat) * 1.5


def test_render_seeded_seed_reproducible():
    p = _mk_profile()
    kw = dict(symbol="rb", timeframe="1d", num_bars=40,
              ref_price=3600.0, amplitude=0.02, seed=123, seed_profile=p)
    a = render_bars_from_script(MarketScript(), **kw)
    b = render_bars_from_script(MarketScript(), **kw)
    assert a == b


def test_render_seeded_volume_magnitude():
    """量能贴合种子中位（几千手量级），不再固定 5000"""
    p = _mk_profile(vol_median=120000.0, vol_log_sigma=0.3, vol_edge_factor=1.2)
    bars = render_bars_from_script(
        MarketScript(volume_pattern="trending", volatility=0.012), symbol="rb",
        timeframe="1d", num_bars=120, ref_price=3600.0,
        amplitude=0.012, seed=5, seed_profile=p,
    )
    vols = [b["volume"] for b in bars]
    assert statistics.median(vols) > 40000
    assert min(vols) > 1000


# ------------------------------------------------------------------
# 历史接入：CSV 目录 / provider 钩子
# ------------------------------------------------------------------

def test_load_seed_bars_from_csv(tmp_path):
    fixture = _mk_daily_bars(120)
    _write_csv(tmp_path / "rb_1d.csv", fixture)
    bars = load_seed_bars_from_csv("rb", "1d", csv_dir=str(tmp_path))
    assert bars is not None and len(bars) == 120
    assert bars[0]["time"] == fixture[0]["time"]
    assert bars[-1]["close"] == pytest.approx(fixture[-1]["close"])
    # 文件不存在 → None
    assert load_seed_bars_from_csv("xx", "1d", csv_dir=str(tmp_path)) is None


def test_load_seed_bars_csv_skips_bad_rows(tmp_path):
    fixture = _mk_daily_bars(100)
    fixture[3]["close"] = 0  # 非法行
    _write_csv(tmp_path / "rb_1d.csv", fixture)
    bars = load_seed_bars_from_csv("rb", "1d", csv_dir=str(tmp_path))
    assert bars is not None and len(bars) == 99


def test_pick_random_window_bounds():
    bars = _mk_daily_bars(300)
    rng = random.Random(1)
    seg = pick_random_window(bars, 60, rng)
    assert seg is not None
    assert MIN_SEED_BARS <= len(seg) <= 180  # max(80, min(n, 60+120))
    # 连续切片
    start = bars.index(seg[0])
    assert bars[start:start + len(seg)] == seg
    assert pick_random_window(bars[:50], 60, rng) is None


async def test_resolve_seed_segment_provider_priority(monkeypatch, tmp_path):
    fixture = _mk_daily_bars(200)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))  # 空 CSV 目录
    calls: list[tuple[str, str]] = []

    async def fake_provider(symbol: str, timeframe: str):
        calls.append((symbol, timeframe))
        return fixture

    monkeypatch.setattr(synthetic_seed, "_provider", fake_provider)
    seg = await resolve_seed_segment("RB", "1d", 60, random.Random(1))
    assert seg is not None and len(seg) >= MIN_SEED_BARS
    assert calls == [("rb", "1d")]  # symbol 归一小写


async def test_resolve_seed_segment_provider_failure_falls_to_csv(monkeypatch, tmp_path):
    fixture = _mk_daily_bars(150)
    _write_csv(tmp_path / "rb_1d.csv", fixture)

    async def boom(symbol: str, timeframe: str):
        raise RuntimeError("my db down")

    monkeypatch.setattr(synthetic_seed, "_provider", boom)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))
    seg = await resolve_seed_segment("rb", "1d", 60, random.Random(1))
    assert seg is not None


async def test_resolve_seed_segment_none_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(synthetic_seed, "_provider", None)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))
    assert await resolve_seed_segment("rb", "1d", 60, random.Random(1)) is None
    # 空 symbol 直接 None
    assert await resolve_seed_segment("", "1d", 60, random.Random(1)) is None


# ------------------------------------------------------------------
# 集成：generate_synthetic_series
# ------------------------------------------------------------------

_SCRIPT_JSON = json.dumps({
    "regime": "bull", "trend_strength": 0.6, "volatility": 0.02,
    "drift_bps": 10, "jumps": [{"at": 0.4, "direction": "down", "size": 0.03}],
    "volume_pattern": "trending",
})


async def test_generate_series_seeded(monkeypatch, tmp_path):
    """接入 provider：meta 标记种子、ref_price=种子末收、提示词含种子摘要"""
    fixture = _mk_daily_bars(300)
    captured: dict[str, Any] = {}

    async def fake_chat(session, user_id, mid, messages, **kw):
        captured["messages"] = messages
        return _SCRIPT_JSON

    async def fake_provider(symbol: str, timeframe: str):
        return fixture

    monkeypatch.setattr("app.services.factor_lab.llm.llm_http.chat_once", fake_chat)
    monkeypatch.setattr(synthetic_seed, "_provider", fake_provider)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))

    series = await generate_synthetic_series(
        None, uuid4(),
        model_row_id=str(uuid4()), symbol="rb", symbol_name="螺纹钢",
        timeframe="1d", num_days=60, ref_price=None, seed=42,
    )
    seed_meta = series.meta["history_seed"]
    assert seed_meta["used"] is True
    times = [b["time"] for b in fixture]
    assert seed_meta["start"] in times and seed_meta["end"] in times  # 随机窗口起止都在源数据内
    assert seed_meta["bars"] >= MIN_SEED_BARS
    # 合成起点价=种子窗口末根收盘（真实量级），而非默认表 3600
    end_bar = next(b for b in fixture if b["time"] == seed_meta["end"])
    assert series.meta["ref_price"] == pytest.approx(end_bar["close"])
    assert len(series.bars) == 60
    user_msg = captured["messages"][1]["content"]
    assert "真实历史种子" in user_msg
    assert "螺纹钢" in user_msg


async def test_generate_series_csv_seeded(monkeypatch, tmp_path):
    """CSV 接入同样生效（无 provider）"""
    fixture = _mk_daily_bars(200)
    _write_csv(tmp_path / "pp_5m.csv", fixture)

    async def fake_chat(session, user_id, mid, messages, **kw):
        return _SCRIPT_JSON

    monkeypatch.setattr("app.services.factor_lab.llm.llm_http.chat_once", fake_chat)
    monkeypatch.setattr(synthetic_seed, "_provider", None)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))

    series = await generate_synthetic_series(
        None, uuid4(),
        model_row_id=str(uuid4()), symbol="pp", symbol_name="pp",
        timeframe="5m", num_days=2, ref_price=None, seed=7,
    )
    assert series.meta["history_seed"]["used"] is True
    assert len(series.bars) == 2 * 48


async def test_generate_series_unconfigured_fallback(monkeypatch, tmp_path):
    """未接入历史：回退纯合成，meta 标记 used=False，生成不中断"""
    async def fake_chat(session, user_id, mid, messages, **kw):
        return _SCRIPT_JSON

    monkeypatch.setattr("app.services.factor_lab.llm.llm_http.chat_once", fake_chat)
    monkeypatch.setattr(synthetic_seed, "_provider", None)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))

    series = await generate_synthetic_series(
        None, uuid4(),
        model_row_id=str(uuid4()), symbol="rb", symbol_name="螺纹钢",
        timeframe="1d", num_days=30, ref_price=None, seed=1,
    )
    assert series.meta["history_seed"]["used"] is False
    assert series.meta["ref_price"] == pytest.approx(3600.0)  # 默认表兜底
    assert len(series.bars) == 30


async def test_generate_series_llm_failure_still_seeded(monkeypatch, tmp_path):
    """LLM 挂掉走默认剧本：种子统计仍在（回退剧本也具备真实量级）"""
    fixture = _mk_daily_bars(200)

    async def bad_chat(session, user_id, mid, messages, **kw):
        raise RuntimeError("llm down")

    async def fake_provider(symbol: str, timeframe: str):
        return fixture

    monkeypatch.setattr("app.services.factor_lab.llm.llm_http.chat_once", bad_chat)
    monkeypatch.setattr(synthetic_seed, "_provider", fake_provider)
    monkeypatch.setenv("QIHUO_SYNTHETIC_SEED_CSV_DIR", str(tmp_path))

    series = await generate_synthetic_series(
        None, uuid4(),
        model_row_id=str(uuid4()), symbol="rb", symbol_name="",
        timeframe="1d", num_days=10, ref_price=None, seed=None,
    )
    assert series.meta["history_seed"]["used"] is True
    assert len(series.bars) == 10  # 默认剧本仍可渲染


def test_default_script_seeded_uses_profile_stats():
    p = _mk_profile(bar_sigma=0.009, amplitude=0.015, gap_mean=0.001, gap_std=0.003)
    for _ in range(5):
        s = _default_script("1d", seed_profile=p)
        # 波动率=种子 bar_sigma（在振幅收敛区间内），跳跃=种子跳空量级
        assert s.volatility == pytest.approx(0.009, abs=5e-5)
        assert 0.007 <= s.jumps[0]["size"] <= 0.08
