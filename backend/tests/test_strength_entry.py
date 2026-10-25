"""strength_entry 策略单测 —— 三档进场做多 / 退出阈值 / short_only / 新鲜度

数据复用 fixtures/strength_cases.json 的 default 用例 bars：
其信号序列为 swing@15 → deep@42/45/48 → rebound@52（已由
test_signal_strength.py 权威验证），本文件按不同截断长度构造持仓与
新鲜度场景。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.ai_trading.strategies import (
    QUANT_STRATEGY_TYPES,
    STRATEGY_LABELS,
    compute_quant_signal,
    normalize_strategy_params,
)
from app.services.ai_trading.strategies.strength_entry import (
    compute_strength_entry_signal,
    normalize_strength_entry_params,
)
from app.services.factor_lab.features import FEATURE_NAMES, feature_matrix

FIXTURE = Path(__file__).parent / "fixtures" / "strength_cases.json"


def _bars(length: int) -> list[dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bars = data["bars"][:length]
    assert len(bars) == length
    return bars


def test_normalize_defaults_and_clamp() -> None:
    p = normalize_strength_entry_params(None)
    assert p["period"] == 14
    assert p["exit_threshold"] == 50.0
    # snake_case（策略层）与 camelCase（图表配置）双口径
    p2 = normalize_strength_entry_params(
        {"period": 99, "trend_ma_period": 30, "exit_threshold": 999.0}
    )
    assert p2["period"] == 99
    assert p2["trend_ma_period"] == 30
    assert p2["exit_threshold"] == 100.0  # clamp
    p3 = normalize_strength_entry_params({"trendMaPeriod": 25})
    assert p3["trend_ma_period"] == 25


def test_insufficient_data() -> None:
    res = compute_strength_entry_signal(_bars(10), {}, "both", None)
    assert res["action"] == "hold"
    assert res["signal"] == "insufficient_data"


def test_open_long_on_fresh_signal() -> None:
    # 截到 bar15（swing 信号恰在末根，freshness=0）→ 开多
    res = compute_strength_entry_signal(_bars(16), {}, "both", None)
    assert res["action"] == "open_long"
    assert res["signal"] == "strength_swing_open_long"
    assert "波段进场" in res["reason"]


def test_freshness_window_is_cooldown() -> None:
    # bar17：swing@15 距今 2 根 ≤ cooldown 3 → 仍可进场
    res = compute_strength_entry_signal(_bars(18), {}, "both", None)
    assert res["action"] == "open_long"
    # bar19：swing@15 距今 4 根 > 3 → 过期 hold
    res2 = compute_strength_entry_signal(_bars(20), {}, "both", None)
    assert res2["action"] == "hold"
    assert res2["signal"] == "stale"


def test_short_only_no_signals() -> None:
    # short_only：无持仓 → hold；持空也不平（不产生任何信号）
    res = compute_strength_entry_signal(_bars(16), {}, "short_only", None)
    assert res["action"] == "hold"
    assert res["signal"] == "long_only_strategy"
    res2 = compute_strength_entry_signal(
        _bars(16), {}, "short_only", {"available_quantity": 2, "direction": "short"}
    )
    assert res2["action"] == "hold"


def test_exit_long_below_threshold() -> None:
    # 持多 + 强弱值低于退出阈值 → 平多优先于进场
    # bars[:43]：钝化平底区，强弱收盘值远低于 50，且 deep@42 新鲜
    res = compute_strength_entry_signal(
        _bars(43),
        {"exit_threshold": 50.0},
        "both",
        {"available_quantity": 3, "direction": "long"},
    )
    assert res["action"] == "close"
    assert res["signal"] == "strength_exit_long"
    assert res["strength_entry_snapshot"]["strength"] < 50.0


def test_hold_long_when_still_strong() -> None:
    # 持多 + 强弱值高于退出阈值 + 无新鲜信号 → hold（已持多仓）
    # bars[:20]：swing@15 已过期（stale 路径之前先走退出判断）
    res = compute_strength_entry_signal(
        _bars(20),
        {"exit_threshold": 20.0},
        "both",
        {"available_quantity": 3, "direction": "long"},
    )
    assert res["action"] == "hold"


def test_registry_dispatch() -> None:
    assert "strength_entry" in QUANT_STRATEGY_TYPES
    assert STRATEGY_LABELS["strength_entry"] == "量化 · 强弱进场"
    p = normalize_strategy_params("strength_entry", {"period": 20})
    assert p["period"] == 20
    res = compute_quant_signal("strength_entry", _bars(16), {}, "both", None)
    assert res["action"] == "open_long"


def test_strength_feature_registered() -> None:
    """STRENGTH 特征 id=35(2026-09-28 桌面端同步批次扩容后不再居末位)"""
    assert FEATURE_NAMES[35] == "STRENGTH"
    assert len(FEATURE_NAMES) == 59
    bars = _bars(60)
    m = feature_matrix(bars)
    from app.services.factor_lab.features import BASE_FEAT_COUNT

    assert m.shape[0] == BASE_FEAT_COUNT  # 无扩展信息源 → 基础行数(40)
    assert m.shape[0] > 35  # STRENGTH 行存在
