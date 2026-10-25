"""strength_entry_v2 策略单测 —— 六档双向 / regime 状态出场 / 新鲜度 / side_mode

数据复用 fixtures/strength_v2_cases.json 的共享主线 bars（default 用例），
其信号与 regime 序列为（标定后默认参数，已由 test_signal_strength_v2.py
权威验证）：top_exhaust@22 → breakdown@27 → bottom_exhaust@38 → rebound@50 →
top_exhaust@57 → continuation_long@66；regime：up 至 26，down 27-49，up 50 起。
本文件按不同截断长度构造持仓、新鲜度与 side_mode 场景。
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
from app.services.ai_trading.strategies.strength_entry_v2 import (
    compute_strength_entry_v2_signal,
    normalize_strength_entry_v2_params,
)

FIXTURE = Path(__file__).parent / "fixtures" / "strength_v2_cases.json"

_LONG = {"available_quantity": 3, "direction": "long"}
_SHORT = {"available_quantity": 2, "direction": "short"}


def _bars(length: int) -> list[dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bars = data["bars"][:length]
    assert len(bars) == length
    return bars


def test_normalize_defaults_and_camel() -> None:
    p = normalize_strength_entry_v2_params(None)
    assert p["period"] == 14 and p["smooth2"] == 2 and p["zone_drop"] == 10.0
    p2 = normalize_strength_entry_v2_params({"period": 20, "zoneDrop": 8, "continuationBand": 6})
    assert p2["period"] == 20 and p2["zone_drop"] == 8 and p2["continuation_band"] == 6


def test_insufficient_data() -> None:
    res = compute_strength_entry_v2_signal(_bars(10), {}, "both", None)
    assert res["action"] == "hold" and res["signal"] == "insufficient_data"


def test_open_short_on_fresh_top_exhaust() -> None:
    # 截到 bar22（top_exhaust 恰在末根，freshness=0，regime=up）→ both 开空
    res = compute_strength_entry_v2_signal(_bars(23), {}, "both", None)
    assert res["action"] == "open_short"
    assert res["signal"] == "strength_v2_top_exhaust_open_short"
    assert "顶部衰竭" in res["reason"]


def test_open_long_on_fresh_rebound() -> None:
    # 截到 bar50（rebound@50 新鲜，regime 已翻 up）→ 开多
    res = compute_strength_entry_v2_signal(_bars(51), {}, "both", None)
    assert res["action"] == "open_long"
    assert res["signal"] == "strength_v2_rebound_open_long"


def test_freshness_window_is_cooldown() -> None:
    # 显式 cooldown=3 验证新鲜度边界：top_exhaust@22 距 bar24 为 2 ≤ 3 可开空；
    # 距 bar26 为 4 > 3 过期（默认冷却已按标定收紧为 8，见第 7 步）
    res = compute_strength_entry_v2_signal(_bars(24), {"cooldown": 3}, "both", None)
    assert res["action"] == "open_short"
    res2 = compute_strength_entry_v2_signal(_bars(27), {"cooldown": 3}, "both", None)
    assert res2["action"] == "hold" and res2["signal"] == "stale"


def test_close_long_via_fresh_short_signal() -> None:
    # 平多路径 1（信号路径）：持多 + top_exhaust@22 新鲜（空侧预警）且 regime
    # 仍为 up（尚未翻转）→ 反向先平。breakdown 场景走 regime 状态路径（见下）
    res = compute_strength_entry_v2_signal(_bars(23), {}, "both", _LONG)
    assert res["action"] == "close"
    assert res["signal"] == "strength_v2_top_exhaust_close_long"


def test_close_long_via_regime_state() -> None:
    # 平多路径 2：持多 + regime=down + 信号已过期（breakdown@27 距 bar32 为 5 > 3）
    # → regime 状态出场兜底（不依赖信号新鲜度）
    res = compute_strength_entry_v2_signal(_bars(33), {}, "both", _LONG)
    assert res["action"] == "close"
    assert res["signal"] == "strength_v2_regime_exit_long"
    snap = res["strength_entry_v2_snapshot"]
    assert snap["regime"] == "down" and snap["last_signal"]["kind"] == "breakdown"


def test_close_short_takes_priority_over_fresh_same_side() -> None:
    # 平空路径 2（regime 状态）优先于信号路径：持空 + regime=up +
    # top_exhaust@22 新鲜（同为空侧）→ 仍先走 regime 状态平空
    res = compute_strength_entry_v2_signal(_bars(23), {}, "both", _SHORT)
    assert res["action"] == "close"
    assert res["signal"] == "strength_v2_regime_exit_short"


def test_close_short_via_fresh_long_signal() -> None:
    # 平空路径 1（信号路径）：持空 + bottom_exhaust@38 新鲜（多侧预警）且
    # regime 仍为 down → 反向先平。rebound 场景走 regime 状态路径（见上）
    res = compute_strength_entry_v2_signal(_bars(39), {}, "both", _SHORT)
    assert res["action"] == "close"
    assert res["signal"] == "strength_v2_bottom_exhaust_close_short"


def test_side_mode_long_only() -> None:
    # long_only：空侧信号不开空（无持仓 hold）；持多遇空侧信号平多
    res = compute_strength_entry_v2_signal(_bars(23), {}, "long_only", None)
    assert res["action"] == "hold" and res["signal"] == "strength_v2_top_exhaust_skip"
    res2 = compute_strength_entry_v2_signal(_bars(23), {}, "long_only", _LONG)
    assert res2["action"] == "close" and res2["signal"] == "strength_v2_top_exhaust_close_long"


def test_side_mode_short_only() -> None:
    # short_only：空侧信号照常开空（与 V1 只做多不同，V2 双向）
    res = compute_strength_entry_v2_signal(_bars(23), {}, "short_only", None)
    assert res["action"] == "open_short"
    # 多侧信号在 short_only 下不开多（无持仓）
    res2 = compute_strength_entry_v2_signal(_bars(51), {}, "short_only", None)
    assert res2["action"] == "hold" and res2["signal"] == "strength_v2_rebound_skip"


def test_no_signal_and_hold_positions() -> None:
    # 无任何信号 → hold；信号过期（默认冷却 8：breakdown@27 距 bar36 为 9 > 8）
    res = compute_strength_entry_v2_signal(_bars(22), {}, "both", None)
    assert res["action"] == "hold" and res["signal"] == "none"
    res2 = compute_strength_entry_v2_signal(_bars(37), {}, "both", None)
    assert res2["action"] == "hold" and res2["signal"] == "stale"


def test_registry_dispatch() -> None:
    assert "strength_entry_v2" in QUANT_STRATEGY_TYPES
    assert STRATEGY_LABELS["strength_entry_v2"] == "量化 · 强弱形态 V2（双向）"
    p = normalize_strategy_params("strength_entry_v2", {"period": 20, "zoneDrop": 8})
    assert p["period"] == 20 and p["zone_drop"] == 8
    res = compute_quant_signal("strength_entry_v2", _bars(23), {}, "both", None)
    assert res["action"] == "open_short"
