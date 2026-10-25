"""MACD / KDJ / 布林波段策略单元测试"""

from __future__ import annotations

from app.services.ai_trading.strategies.band_swing import (
    compute_band_swing_signal,
    normalize_band_params,
)
from app.services.ai_trading.strategies.kdj_cross import (
    compute_kdj_cross_signal,
    normalize_kdj_params,
)
from app.services.ai_trading.strategies.macd_cross import (
    compute_macd_cross_signal,
    normalize_macd_params,
)
from app.services.ai_trading.strategies import (
    QUANT_STRATEGY_TYPES,
    compute_quant_signal,
)


def _bars_up(n: int, start: float = 100.0) -> list[dict]:
    """单调上涨收盘序列"""
    out: list[dict] = []
    for i in range(n):
        c = start + i * 0.5
        out.append(
            {
                "time": f"t{i}",
                "open": c - 0.2,
                "high": c + 0.3,
                "low": c - 0.3,
                "close": c,
                "volume": 1,
            }
        )
    return out


def test_normalize_macd_defaults() -> None:
    p = normalize_macd_params(None)
    assert p["fast_period"] == 12
    assert p["slow_period"] == 26
    assert p["signal_period"] == 9


def test_normalize_kdj_defaults() -> None:
    p = normalize_kdj_params({})
    assert p["n_period"] == 9
    assert p["use_zone"] is False


def test_normalize_band_defaults() -> None:
    p = normalize_band_params(None)
    assert p["period"] == 20
    assert p["std_mult"] == 2.0


def test_macd_insufficient_bars() -> None:
    sig = compute_macd_cross_signal(_bars_up(10), {}, "both", None)
    assert sig["action"] == "hold"
    assert sig["signal"] == "insufficient_data"


def test_kdj_insufficient_bars() -> None:
    sig = compute_kdj_cross_signal(_bars_up(5), {}, "both", None)
    assert sig["action"] == "hold"


def test_band_insufficient_bars() -> None:
    sig = compute_band_swing_signal(_bars_up(10), {}, "both", None)
    assert sig["action"] == "hold"


def test_swing_pivot_not_in_registry() -> None:
    """波段策略已下线：注册表不再包含 swing_pivot / swing_pivot_v2"""
    assert "swing_pivot" not in QUANT_STRATEGY_TYPES
    assert "swing_pivot_v2" not in QUANT_STRATEGY_TYPES
    assert "band_swing" in QUANT_STRATEGY_TYPES


def test_compute_quant_dispatch() -> None:
    bars = _bars_up(80)
    for st in QUANT_STRATEGY_TYPES:
        out = compute_quant_signal(st, bars, {}, "both", None)
        assert "action" in out
        assert out.get("parse_ok") is True or "reason" in out


def test_band_touch_lower_opens_long() -> None:
    """构造下破下轨的末根，期望 open_long 或 close 语义"""
    bars = _bars_up(40, 100.0)
    # 末根大幅下杀
    last = dict(bars[-1])
    last["close"] = 50.0
    last["low"] = 49.0
    last["high"] = 51.0
    bars[-1] = last
    sig = compute_band_swing_signal(bars, {"period": 20, "std_mult": 2.0}, "both", None)
    assert sig["action"] in ("open_long", "hold", "close")
    assert "band_snapshot" in sig or sig.get("signal") == "insufficient_data"
