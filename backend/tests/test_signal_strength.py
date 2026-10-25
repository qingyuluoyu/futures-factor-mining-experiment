"""强弱指标单测 —— 本文件是算法的**权威验证**（规格见 discuss/2026-08-31 第三节）

- 本文件独立验证归一化 / 平滑四性质 / 三档触发与不触发 / 优先级 / 冷却 /
  因果重放不撤销；
- frontend/src/lib/strength-index.test.mjs 共读同一份
  fixtures/strength_cases.json，证明 TS 实现与本侧逐位一致。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.services.signal_strength import (
    DEFAULT_STRENGTH_PARAMS,
    calc_strength,
    normalize_strength_params,
)

FIXTURE = Path(__file__).parent / "fixtures" / "strength_cases.json"


def _bars_from_fixture() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["bars"], data


def _flat_bars(n: int, price: float = 88.0, wick: float = 0.0) -> list[dict[str, Any]]:
    """平台段：open=close=price，影线宽度可调"""
    return [
        {
            "time": f"t{i}",
            "open": price,
            "high": price + wick,
            "low": price - wick,
            "close": price,
            "volume": 1000.0,
        }
        for i in range(n)
    ]


def _ramp_bars(n: int, step: float, start: float = 100.0, wick: float = 0.3) -> list[dict[str, Any]]:
    """单调序列：open=前根 close，close±step"""
    bars: list[dict[str, Any]] = []
    prev = start
    for i in range(n):
        o = prev
        c = prev + step
        bars.append(
            {
                "time": f"t{i}",
                "open": o,
                "high": max(o, c) + wick,
                "low": min(o, c) - wick,
                "close": c,
                "volume": 1000.0,
            }
        )
        prev = c
    return bars


# ---------- fixture 逐位对齐（TS ↔ Python，1e-9）----------


def test_fixture_matches_implementation() -> None:
    """共享 fixture 全用例逐位一致（前后端同源的权威守护）"""
    bars, data = _bars_from_fixture()
    tol = 1e-9
    assert len(data["cases"]) >= 6
    for case in data["cases"]:
        params = normalize_strength_params(case["params"])
        res = calc_strength(bars, params)
        exp_points = case["expected_points"]
        assert len(res["points"]) == len(exp_points), case["name"]
        for k, (got, exp) in enumerate(zip(res["points"], exp_points)):
            assert exp["index"] == params["period"] - 1 + k
            assert got["time"] == bars[exp["index"]]["time"]
            for key in ("open", "high", "low", "close"):
                assert abs(got[key] - exp[key]) < tol, f"{case['name']} 点[{k}].{key}: {got[key]} != {exp[key]}"
        exp_signals = case["expected_signals"]
        assert len(res["signals"]) == len(exp_signals), case["name"]
        for got, exp in zip(res["signals"], exp_signals):
            assert got["index"] == exp["index"]
            assert got["time"] == bars[exp["index"]]["time"]
            assert got["kind"] == exp["kind"]
            assert abs(got["value"] - exp["value"]) < tol
            assert got["pending"] == exp["pending"]


def test_normalize_params_camel_to_snake() -> None:
    """camelCase 入参映射 + 缺省回落"""
    full = normalize_strength_params(
        {
            "period": 20, "smooth": 4, "smooth2": 2, "trendMaPeriod": 30,
            "swingThreshold": 55, "reboundThreshold": 45, "oversoldLevel": 15,
            "reboundLookback": 8, "deepLevel": 4, "deepBars": 2, "cooldown": 6,
        }
    )
    assert full["period"] == 20
    assert full["trend_ma_period"] == 30
    assert full["swing_threshold"] == 55
    partial = normalize_strength_params({"period": 9})
    assert partial["period"] == 9
    assert partial["smooth"] == DEFAULT_STRENGTH_PARAMS["smooth"]
    assert normalize_strength_params(None) == dict(DEFAULT_STRENGTH_PARAMS)


# ---------- 归一化 ----------


def test_warmup_boundary() -> None:
    """窗口不满不输出：n<period 空；n==period 恰 1 点"""
    bars, _ = _bars_from_fixture()
    empty = calc_strength(bars[:13], normalize_strength_params({"period": 14}))
    assert empty["points"] == []
    assert empty["signals"] == []
    one = calc_strength(bars[:14], normalize_strength_params({"period": 14}))
    assert len(one["points"]) == 1
    assert one["signals"] == []


def test_range_zero_takes_50() -> None:
    """一字板 range==0 → raw 取 50，任意平滑参数下全序列恒 50"""
    for extra in ({}, {"smooth": 5, "smooth2": 4, "period": 7}):
        params = normalize_strength_params(extra)
        res = calc_strength(_flat_bars(30), params)
        assert res["points"]
        for p in res["points"]:
            assert p["open"] == 50.0
            assert p["high"] == 50.0
            assert p["low"] == 50.0
            assert p["close"] == 50.0


# ---------- 平滑四性质 ----------


def test_properties_on_fixture_all_cases() -> None:
    """连续 / 保序 / 不越界，覆盖全部 fixture 用例"""
    bars, data = _bars_from_fixture()
    for case in data["cases"]:
        res = calc_strength(bars, normalize_strength_params(case["params"]))
        pts = res["points"]
        for k, p in enumerate(pts):
            assert p["high"] >= p["close"], f"{case['name']} high<close @k={k}"
            assert p["close"] >= p["low"], f"{case['name']} close<low @k={k}"
            for key in ("open", "high", "low", "close"):
                assert 0 <= p[key] <= 100, f"{case['name']} {key}={p[key]} 越界"
            if k > 0:
                assert p["open"] == pts[k - 1]["close"], f"{case['name']} 蜡烛不连续 @k={k}"


def test_red_green_semantics() -> None:
    """红青语义：单调涨全红（close≥open），单调跌全青（close≤open）"""
    up = calc_strength(_ramp_bars(60, 1.2), normalize_strength_params(None))
    assert all(p["close"] >= p["open"] for p in up["points"])
    down = calc_strength(_ramp_bars(60, -1.2), normalize_strength_params(None))
    assert all(p["close"] <= p["open"] for p in down["points"])


# ---------- 三档信号：触发与不触发 ----------


def test_swing_requires_above_ma() -> None:
    """swing 需 close > SMA：S 上穿 50 但 close 低于趋势均线时不触发。

    构造：平台 100 → 快跌到 40 → 缓修复。趋势均线取 30（窗口被平台期高价
    抬高），修复段 close 全程 < MA30；S 在修复中段上穿 50 —— 不得出 swing。
    用输出点序列（smooth2=1 时即主序列）确认 50 上穿确实发生。
    """
    bars = _flat_bars(16, 100.0, wick=0.3)
    bars += _ramp_bars(10, -6.0, wick=0.3)  # 100 → 40
    bars += _ramp_bars(14, 1.0, wick=0.3)  # 40 → 54，S 上穿 50
    params = normalize_strength_params(
        {"trendMaPeriod": 30, "reboundThreshold": 101, "deepLevel": -1}
    )
    res = calc_strength(bars, params)
    # 确认主序列确实从 <50 上穿到 ≥50（测试有效性的前提）
    closes = [p["close"] for p in res["points"]]
    assert any(a < 50 <= b for a, b in zip(closes, closes[1:])), closes
    closes30 = [float(b["close"]) for b in bars]
    for s in res["signals"]:
        ma30 = sum(closes30[s["index"] - 29 : s["index"] + 1]) / 30
        assert closes30[s["index"]] < ma30  # 多头条件不成立
        assert s["kind"] != "swing", s
    assert not any(s["kind"] == "swing" and not s["pending"] for s in res["signals"])


def test_rebound_requires_recent_oversold() -> None:
    """rebound 需近期到过弱区：S 全程 > 20 的浅回撤上穿 50 不触发 rebound。

    构造：平台 100 → 宽影线缓跌平台 97（raw 停在 20~50 区间，S 不进弱区）
    → 宽影线缓涨上穿 50 —— 只允许 swing，不得出现 rebound。
    """
    bars = _flat_bars(16, 100.0, wick=0.3)
    bars += _flat_bars(8, 97.0, wick=2.5)  # raw ≈ 43，S 不进弱区
    bars += _ramp_bars(20, 0.4, wick=2.5)  # 97 → 105，S 上穿 50
    params = normalize_strength_params({"oversoldLevel": 20})
    res = calc_strength(bars, params)
    closed = [s for s in res["signals"] if not s["pending"]]
    kinds = {s["kind"] for s in closed}
    assert "rebound" not in kinds, closed
    assert "swing" in kinds, closed  # 对照组：同序列 swing 应触发


def test_deep_and_cooldown_from_fixture() -> None:
    """default：钝化区拐头连发被冷却按 3 根间隔放行（42/45/48）；
    cooldown=10 时 45/48 被挡，仅剩 42。"""
    bars, data = _bars_from_fixture()
    default = next(c for c in data["cases"] if c["name"] == "default")
    deeps = [s["index"] for s in default["expected_signals"] if s["kind"] == "deep"]
    assert deeps == [42, 45, 48], deeps
    cd = next(c for c in data["cases"] if c["name"] == "deep_cooldown")
    deeps_cd = [s["index"] for s in cd["expected_signals"] if s["kind"] == "deep"]
    assert deeps_cd == [42], deeps_cd


def test_priority_rebound_over_swing() -> None:
    """优先级：bar52 同时满足 rebound 与 swing 条件，只出 rebound。

    手工核对 bar52 的 swing 侧条件确实成立（close > SMA10），
    证明是优先级裁决而非 swing 条件不满足。
    """
    bars, _ = _bars_from_fixture()
    params = normalize_strength_params(None)
    res = calc_strength(bars, params)
    by_index = {s["index"]: s for s in res["signals"]}
    assert 52 in by_index and by_index[52]["kind"] == "rebound"
    closes = [float(b["close"]) for b in bars]
    ma10 = sum(closes[43:53]) / 10
    assert closes[52] > ma10  # swing 的多头条件在 bar52 同样成立


def test_one_signal_per_bar() -> None:
    """同一根 bar 至多一个信号（全 fixture 用例）"""
    _, data = _bars_from_fixture()
    for case in data["cases"]:
        seen: set[int] = set()
        for s in case["expected_signals"]:
            assert s["index"] not in seen, (case["name"], s)
            seen.add(s["index"])


# ---------- 因果性 ----------


def test_causal_replay_no_repaint() -> None:
    """逐前缀重放：已收盘信号与全量运行逐项一致（永不撤销）"""
    bars, _ = _bars_from_fixture()
    params = normalize_strength_params(None)
    full = calc_strength(bars, params)
    for length in (20, 30, 40, 45, 50, 55, 60, 65, 70, 76):
        prefix = calc_strength(bars[:length], params)
        prefix_closed = [s for s in prefix["signals"] if not s["pending"]]
        full_closed = [s for s in full["signals"] if s["index"] <= length - 2]
        assert prefix_closed == full_closed, f"前缀 L={length} 重放不一致"


def test_pending_marks_last_bar() -> None:
    """已知信号落到序列末根时 pending=True"""
    bars, _ = _bars_from_fixture()
    params = normalize_strength_params(None)
    res = calc_strength(bars[:16], params)
    swings = [s for s in res["signals"] if s["kind"] == "swing"]
    assert len(swings) == 1
    assert swings[0]["index"] == 15
    assert swings[0]["pending"] is True


# ---------- 数值精度 ----------


def test_round2_half_up() -> None:
    """half-up 而非银行家舍入：0.125 → 0.13"""
    from app.services.signal_strength import _round2

    assert _round2(0.125) == 0.13
    assert _round2(2.675) == 2.68
    assert _round2(50.0) == 50.0
    assert math.floor(0.125 * 100 + 0.5) / 100 == 0.13
