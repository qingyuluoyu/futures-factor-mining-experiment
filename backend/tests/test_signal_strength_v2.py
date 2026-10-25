"""强弱指标 V2 单测 —— 本文件是算法的**权威验证**（规格见 V2 设计文档 4.2-4.7）

分工同 test_signal_strength.py：
- 本文件独立验证 fixture 逐位对齐（TS ↔ Python，1e-9）与六档形态档的
  触发/不触发、互斥与优先级、滞回带、价格确认、ATR 预热、因果重放；
- frontend/src/lib/strength-v2.test.mjs / strength-v2-spec.test.mjs 共读
  同一份 fixtures/strength_v2_cases.json，证明 TS 实现与本侧逐位一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.signal_strength_v2 import (
    DEFAULT_STRENGTH_V2_PARAMS,
    calc_strength_v2,
    normalize_strength_v2_params,
)

FIXTURE = Path(__file__).parent / "fixtures" / "strength_v2_cases.json"
TOL = 1e-9


def _load() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data["bars"], data


def _case_bars(case: dict[str, Any], shared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return case.get("bars", shared)


def _by_name(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in data["cases"]}


# ---------- fixture 逐位对齐（TS ↔ Python，1e-9）----------


def test_fixture_matches_implementation() -> None:
    """共享 fixture 全用例逐位一致（前后端同源的权威守护，含 regimes）"""
    shared, data = _load()
    assert len(data["cases"]) == 12
    for case in data["cases"]:
        params = normalize_strength_v2_params(case["params"])
        res = calc_strength_v2(_case_bars(case, shared), params)
        exp_points = case["expected_points"]
        assert len(res["points"]) == len(exp_points), case["name"]
        for k, (got, exp) in enumerate(zip(res["points"], exp_points)):
            assert exp["index"] == params["period"] - 1 + k
            assert got["time"] == _case_bars(case, shared)[exp["index"]]["time"]
            for key in ("open", "high", "low", "close"):
                assert abs(got[key] - exp[key]) < TOL, f"{case['name']} 点[{k}].{key}"
        exp_signals = case["expected_signals"]
        assert len(res["signals"]) == len(exp_signals), case["name"]
        for got, exp in zip(res["signals"], exp_signals):
            assert got["index"] == exp["index"]
            assert got["kind"] == exp["kind"]
            assert abs(got["value"] - exp["value"]) < TOL
            assert got["pending"] == exp["pending"]
        assert res["regimes"] == case["expected_regimes"], case["name"]


def test_normalize_params_camel_to_snake() -> None:
    """camelCase 入参映射 + 缺省回落"""
    full = normalize_strength_v2_params(
        {
            "period": 20, "smooth": 4, "smooth2": 3, "continuationBand": 8,
            "exhaustWindow": 5, "shrinkRatio": 0.5, "flatEps": 1.5, "zoneWindow": 8,
            "lowZone": 30, "highZone": 70, "priceBufferAtrMult": 0.5,
            "atrPeriod": 20, "cooldown": 6,
        }
    )
    assert full["continuation_band"] == 8
    assert full["price_buffer_atr_mult"] == 0.5
    partial = normalize_strength_v2_params({"period": 9})
    assert partial["period"] == 9
    assert partial["smooth2"] == DEFAULT_STRENGTH_V2_PARAMS["smooth2"]
    assert normalize_strength_v2_params(None) == dict(DEFAULT_STRENGTH_V2_PARAMS)


# ---------- 归一化与预热 ----------


def test_warmup_boundary() -> None:
    """窗口不满不输出：n<period 空；n==period 恰 1 点"""
    shared, _ = _load()
    params = normalize_strength_v2_params({"period": 14})
    empty = calc_strength_v2(shared[:13], params)
    assert empty["points"] == [] and empty["signals"] == [] and empty["regimes"] == []
    one = calc_strength_v2(shared[:14], params)
    assert len(one["points"]) == 1 and one["signals"] == []


def test_range_zero_takes_50() -> None:
    """一字板 range==0 → raw 取 50，全序列恒 50 且 regime 恒 none"""
    bars = [
        {"time": f"t{i}", "open": 88.0, "high": 88.0, "low": 88.0, "close": 88.0, "volume": 1.0}
        for i in range(30)
    ]
    res = calc_strength_v2(bars, normalize_strength_v2_params(None))
    assert res["points"]
    for p in res["points"]:
        assert p["open"] == 50.0 and p["high"] == 50.0 and p["low"] == 50.0 and p["close"] == 50.0
    assert all(r == "none" for r in res["regimes"])


# ---------- 通用性质（全部用例）----------


def test_properties_all_cases() -> None:
    """蜡烛连续 / 保序界内 / 判定=显示 / 冷却与同根唯一"""
    shared, data = _load()
    for case in data["cases"]:
        params = normalize_strength_v2_params(case["params"])
        pts = case["expected_points"]
        for k, p in enumerate(pts):
            assert p["high"] >= p["close"] >= p["low"], f"{case['name']} 保序破坏 @k={k}"
            for key in ("open", "high", "low", "close"):
                assert 0 <= p[key] <= 100, f"{case['name']} {key}={p[key]} 越界"
            if k > 0:
                assert p["open"] == pts[k - 1]["close"], f"{case['name']} 蜡烛不连续 @k={k}"
        last: dict[str, float] = {}
        seen: set[int] = set()
        for s in case["expected_signals"]:
            # 判定=显示（第三节地基）：信号 value ≡ 触发 bar 输出点 close
            k = s["index"] - (params["period"] - 1)
            assert s["value"] == pts[k]["close"], f"{case['name']} 信号@{s['index']} 判定序列 ≠ 显示序列"
            assert s["index"] - last.get(s["kind"], -float("inf")) >= params["cooldown"]
            last[s["kind"]] = s["index"]
            assert s["index"] not in seen, f"{case['name']} bar {s['index']} 多信号"
            seen.add(s["index"])


def test_causal_replay_no_repaint() -> None:
    """逐前缀重放：已收盘信号与全量运行逐项一致（永不撤销）"""
    shared, data = _load()
    case = _by_name(data)["default"]
    params = normalize_strength_v2_params(case["params"])
    bars = _case_bars(case, shared)
    full = calc_strength_v2(bars, params)
    for length in (20, 30, 40, 50, 60, 70, 79):
        prefix = calc_strength_v2(bars[:length], params)
        prefix_closed = [s for s in prefix["signals"] if not s["pending"]]
        full_closed = [s for s in full["signals"] if s["index"] <= length - 2]
        assert prefix_closed == full_closed, f"前缀 L={length} 重放不一致"


def test_pending_marks_last_bar() -> None:
    """已知信号落到序列末根时 pending=True"""
    shared, data = _load()
    case = _by_name(data)["bottom_exhaust_flat"]
    params = normalize_strength_v2_params(case["params"])
    res = calc_strength_v2(_case_bars(case, shared)[:23], params)
    sigs = [s for s in res["signals"] if s["kind"] == "bottom_exhaust"]
    assert len(sigs) == 1 and sigs[0]["index"] == 22 and sigs[0]["pending"] is True


# ---------- 六档形态档：触发与不触发（8.1）----------


def test_hysteresis_hold() -> None:
    """滞回带：S 全程徘徊带内，regime 恒 none、零信号"""
    _, data = _load()
    case = _by_name(data)["hysteresis_hold"]
    assert case["expected_signals"] == []
    assert all(r == "none" for r in case["expected_regimes"])
    assert all(45 < p["close"] < 55 for p in case["expected_points"])


def test_regime_init_silent() -> None:
    """none→up 初始化不出 rebound；首个信号在真正的 up→down 翻转"""
    _, data = _load()
    case = _by_name(data)["regime_init_silent"]
    first = case["params"]["period"] - 1
    assert case["expected_regimes"][0] == "up"
    assert not any(s["index"] == first for s in case["expected_signals"])
    assert [s["kind"] for s in case["expected_signals"]] == ["breakdown", "bottom_exhaust", "rebound"]


def test_flip_bar_not_pullback() -> None:
    """区分 >= 与 > 的强守护，不得弱化或删除（4.3 ② 陷阱）。

    翻转根深下影低点 108.5 远低于其后回调低点 109.4，锤头候选根下影 107.6
    落在两阈值之间：>= 写法 pullbackLowBefore=109.4 拒绝；> 写法翻转根低点
    滞留 pullback（108.5）会放行 continuation_long@24，fixture 对齐必挂。
    """
    _, data = _load()
    case = _by_name(data)["flip_bar_not_pullback"]
    assert len(case["expected_signals"]) == 1
    assert case["expected_signals"][0] == {"index": 20, "kind": "rebound", "value": 58.66, "pending": False}
    closes = [p["close"] for p in case["expected_points"]]
    k = 24 - (case["params"]["period"] - 1)
    d, dprev = closes[k] - closes[k - 1], closes[k - 1] - closes[k - 2]
    assert d > 0 and dprev <= 0  # i=24 确为 turnUp 根（断言有效性前提）
    assert not any(s["index"] == 24 for s in case["expected_signals"])


def test_priority_flip() -> None:
    """翻转根同时满足 turnUp → 只出 rebound 不出中继（4.5 优先级）"""
    _, data = _load()
    case = _by_name(data)["priority_flip"]
    by_index = {s["index"]: s["kind"] for s in case["expected_signals"]}
    assert by_index == {22: "continuation_short", 23: "rebound"}
    closes = [p["close"] for p in case["expected_points"]]
    k = 23 - (case["params"]["period"] - 1)
    assert closes[k] - closes[k - 1] > 0 and closes[k - 1] - closes[k - 2] <= 0


def test_continuation_price_reject() -> None:
    """reject 根 turnUp 成立但创回调新低 → 不触发；守住后触发"""
    _, data = _load()
    case = _by_name(data)["continuation_price_reject"]
    by_index = {s["index"]: s["kind"] for s in case["expected_signals"]}
    assert 19 not in by_index
    assert by_index.get(21) == "continuation_long"


def test_no_pullback_and_atr_warmup() -> None:
    """无回调不出中继；ATR 预热段不降级（不出任何信号，生效后正常触发）"""
    _, data = _load()
    by = _by_name(data)
    nb = by["no_pullback"]
    assert not any(s["kind"].startswith("continuation_") for s in nb["expected_signals"])
    assert [s["kind"] for s in nb["expected_signals"]] == ["rebound"]
    aw = by["atr_warmup"]
    atr_period = aw["params"]["atrPeriod"]
    assert not any(s["index"] < atr_period for s in aw["expected_signals"])
    # top_exhaust@26 恰在 ATR 生效边界触发，顺带证明衰竭档同受 ATR 门控
    assert [s["kind"] for s in aw["expected_signals"]] == ["top_exhaust", "continuation_long"]


def test_bottom_exhaust_two_paths() -> None:
    """flat 走平路径与 shrink 收缩比路径各自独立成立（4.4）"""
    _, data = _load()
    by = _by_name(data)
    eps = DEFAULT_STRENGTH_V2_PARAMS["flat_eps"]
    limit = DEFAULT_STRENGTH_V2_PARAMS["shrink_ratio"]
    m = DEFAULT_STRENGTH_V2_PARAMS["exhaust_window"]
    # 方案一后衰竭窗不含信号根：now = [k−m, k−1]，prev = [k−2m, k−m−1]（m 通用）
    for name, sig_idx, want_flat in (("bottom_exhaust_flat", 22, True), ("bottom_exhaust_shrink", 23, False)):
        case = by[name]
        closes = [p["close"] for p in case["expected_points"]]
        k = sig_idx - (case["params"]["period"] - 1)
        bodies = [abs(closes[j] - closes[j - 1]) for j in range(k - 2 * m, k)]
        now_mean = sum(bodies[m:]) / m
        prev_mean = sum(bodies[:m]) / m
        all_small = all(b <= eps for b in bodies[m:])
        shrink_ok = prev_mean > 0 and now_mean / prev_mean <= limit
        if want_flat:
            assert all_small and not shrink_ok
        else:
            assert not all_small and shrink_ok


def test_atr_buffer_boundary_inclusive() -> None:
    """ATR 缓冲边界：转红柱低点恰等于回调低点（≥ 含等号）时中继照常触发"""
    bars: list[dict[str, Any]] = [
        {"time": "t0", "open": 100.0, "high": 100.5, "low": 99.6, "close": 100.2}
    ]

    def push(step: float, wick: float) -> None:
        o = bars[-1]["close"]
        c = round(o + step, 2)
        bars.append(
            {
                "time": f"t{len(bars)}",
                "open": o,
                "high": max(o, c) + wick,
                "low": min(o, c) - wick,
                "close": c,
            }
        )

    for st in (1.0, 1.0, 1.1, 1.1, 1.2, 1.2):
        push(st, 0.2)
    o1 = bars[-1]["close"]
    bars.append({"time": "t7", "open": o1, "high": o1 - 0.8, "low": o1 - 1.4, "close": o1 - 1.0})
    o2 = bars[-1]["close"]
    bars.append({"time": "t8", "open": o2, "high": o2 + 1.4, "low": o2 - 0.4, "close": o2 + 1.2})
    push(1.0, 0.2)
    push(1.0, 0.2)
    assert bars[7]["low"] == bars[8]["low"]
    res = calc_strength_v2(bars, normalize_strength_v2_params({"period": 4, "atrPeriod": 2}))
    assert any(s["kind"] == "continuation_long" for s in res["signals"])
