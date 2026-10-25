"""行情筛选引擎：条件评估纯函数 + 全市场扫描（mock K 线）"""

from __future__ import annotations

from typing import Any

import pytest

import app.services.market_screener as screener_mod
from app.services.market_screener import (
    condition_lookback,
    evaluate_conditions,
    run_screener,
    scan_symbol_screener,
)


def bars_from_closes(closes: list[float], high_pad: float = 0.5, low_pad: float = 0.5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, c in enumerate(closes):
        out.append(
            {
                "time": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": c,
                "high": c + high_pad,
                "low": c - low_pad,
                "close": c,
                "volume": 100,
            }
        )
    return out


def rising(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


def falling(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start - i * step for i in range(n)]


def accel(n: int, start: float = 100.0) -> list[float]:
    """加速上涨：线性序列上 DIF/DEA 会收敛相等，需曲率拉开差值。"""
    return [start + i * i * 0.01 for i in range(n)]


# ---------- MACD ----------


def test_macd_golden_on_last_bar() -> None:
    closes = [50.0] * 30 + [50.0, 51.2]
    bars = bars_from_closes(closes)
    sig_i = len(bars) - 1
    res = evaluate_conditions(
        bars, sig_i, [{"type": "macd", "op": "golden", "window": 1}]
    )
    assert res[0]["passed"] is True
    assert "金叉" in res[0]["label"] or "金叉" in res[0]["detail"]


def test_macd_golden_window_catches_older_cross() -> None:
    # 交叉发生在倒数第 2 根：window=1 不命中，window=3 命中
    closes = [50.0] * 30 + [50.0, 51.2, 53.0]
    bars = bars_from_closes(closes)
    sig_i = len(bars) - 1
    res_w1 = evaluate_conditions(bars, sig_i, [{"type": "macd", "op": "golden", "window": 1}])
    res_w3 = evaluate_conditions(bars, sig_i, [{"type": "macd", "op": "golden", "window": 3}])
    assert res_w1[0]["passed"] is False
    assert res_w3[0]["passed"] is True


def test_macd_death_on_last_bar() -> None:
    closes = [50.0] * 30 + [50.0, 48.8]
    bars = bars_from_closes(closes)
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "macd", "op": "death", "window": 1}]
    )
    assert res[0]["passed"] is True


def test_macd_dif_state() -> None:
    bars = bars_from_closes(accel(60))
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "macd", "op": "dif_above"}]
    )
    assert res[0]["passed"] is True
    res2 = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "macd", "op": "dif_below"}]
    )
    assert res2[0]["passed"] is False


# ---------- 均线 ----------


def test_ma_bull_arrange_passes_on_uptrend() -> None:
    bars = bars_from_closes(rising(60, step=0.5))
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "ma", "op": "bull_arrange"}]
    )
    assert res[0]["passed"] is True
    res_bear = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "ma", "op": "bear_arrange"}]
    )
    assert res_bear[0]["passed"] is False


def test_ma_bear_arrange_passes_on_downtrend() -> None:
    bars = bars_from_closes(falling(60, step=0.5))
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "ma", "op": "bear_arrange"}]
    )
    assert res[0]["passed"] is True


def test_ma_cross_above_on_last_bar() -> None:
    closes = [100.0] * 20 + [90.0] * 5 + [95.0]
    bars = bars_from_closes(closes)
    sig_i = len(bars) - 1
    # cross_period=5：前一根 close90<=MA5=90，本根 close95>MA5=91
    res = evaluate_conditions(
        bars, sig_i, [{"type": "ma", "op": "cross_above", "cross_period": 5, "window": 1}]
    )
    assert res[0]["passed"] is True


def test_ma_arrange_custom_periods() -> None:
    bars = bars_from_closes(rising(120, step=0.3))
    res = evaluate_conditions(
        bars,
        len(bars) - 1,
        [{"type": "ma", "op": "bull_arrange", "ma_short": 10, "ma_mid": 30, "ma_long": 60}],
    )
    assert res[0]["passed"] is True


# ---------- KDJ ----------


def test_kdj_golden_after_turn() -> None:
    closes = [100.0] * 40
    closes += [100.0 + j * 2.0 for j in range(8)]
    bars = bars_from_closes(closes, high_pad=1.0, low_pad=1.0)
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "kdj", "op": "golden", "window": 8}]
    )
    assert res[0]["passed"] is True


def test_kdj_overbought_on_strong_rally() -> None:
    bars = bars_from_closes(rising(50, step=1.5), high_pad=1.0, low_pad=1.0)
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "kdj", "op": "overbought", "overbought": 80}]
    )
    assert res[0]["passed"] is True


# ---------- RSI ----------


def test_rsi_overbought_on_rally() -> None:
    bars = bars_from_closes(rising(40, step=1.0))
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "rsi", "op": "overbought", "overbought": 70}]
    )
    assert res[0]["passed"] is True


def test_rsi_oversold_on_selloff() -> None:
    bars = bars_from_closes(falling(40, step=1.0))
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "rsi", "op": "oversold", "oversold": 30}]
    )
    assert res[0]["passed"] is True


# ---------- BOLL ----------


def test_boll_break_upper_on_spike() -> None:
    closes = [100.0] * 40 + [106.0]
    bars = bars_from_closes(closes)
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "boll", "op": "break_upper", "window": 1}]
    )
    assert res[0]["passed"] is True


def test_boll_above_middle_on_uptrend() -> None:
    bars = bars_from_closes(rising(60, step=0.4))
    res = evaluate_conditions(
        bars, len(bars) - 1, [{"type": "boll", "op": "above_middle"}]
    )
    assert res[0]["passed"] is True


# ---------- 组合 ----------


def test_combined_conditions_all_must_pass() -> None:
    bars = bars_from_closes(rising(60, step=0.5))
    sig_i = len(bars) - 1
    both = evaluate_conditions(
        bars,
        sig_i,
        [
            {"type": "ma", "op": "bull_arrange"},
            {"type": "rsi", "op": "overbought", "overbought": 70},
        ],
    )
    assert all(r["passed"] for r in both)

    mixed = evaluate_conditions(
        bars,
        sig_i,
        [
            {"type": "ma", "op": "bull_arrange"},
            {"type": "ma", "op": "bear_arrange"},
        ],
    )
    assert not all(r["passed"] for r in mixed)


def test_unknown_condition_type_fails_gracefully() -> None:
    bars = bars_from_closes(rising(30))
    res = evaluate_conditions(bars, 29, [{"type": "nope", "op": "x"}])
    assert res[0]["passed"] is False
    assert "未知" in res[0]["detail"]


def test_lookback_grows_with_params() -> None:
    base = condition_lookback([{"type": "macd", "op": "golden"}], "1d")
    big = condition_lookback(
        [{"type": "ma", "op": "bull_arrange", "ma_long": 200}], "1d"
    )
    assert big > base
    assert big <= 500


# ---------- 单品种扫描（mock K 线） ----------


@pytest.mark.asyncio
async def test_scan_symbol_screener_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = rising(80, step=0.5)

    async def fake_kline(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return bars_from_closes(closes)

    monkeypatch.setattr(screener_mod, "get_kline_history", fake_kline)
    item = await scan_symbol_screener(
        None, "rb2610", "螺纹钢", "1d", [{"type": "ma", "op": "bull_arrange"}]
    )
    assert item is not None
    assert item["symbol"] == "rb2610"
    assert item["direction"] == "long"
    assert item["conditions"][0]["passed"] is True


@pytest.mark.asyncio
async def test_scan_symbol_screener_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    closes = falling(80, step=0.5)

    async def fake_kline(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return bars_from_closes(closes)

    monkeypatch.setattr(screener_mod, "get_kline_history", fake_kline)
    item = await scan_symbol_screener(
        None, "cu2610", "铜", "1d", [{"type": "ma", "op": "bull_arrange"}]
    )
    assert item is None


# ---------- 全市场扫描（mock 宇宙 + K 线） ----------


@pytest.mark.asyncio
async def test_run_screener_filters_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    up = bars_from_closes(accel(80))
    down = bars_from_closes(falling(80, step=0.5))

    async def fake_masters(redis_conn: Any) -> list[dict[str, str]]:
        return [
            {"product_code": "RB", "symbol": "rb2610", "name": "螺纹钢",
             "product_name": "螺纹钢", "contract_name": "螺纹钢2610"},
            {"product_code": "CU", "symbol": "cu2610", "name": "铜",
             "product_name": "铜", "contract_name": "铜2610"},
        ]

    async def fake_kline(
        redis_conn: Any, symbol: str, *args: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return up if symbol.startswith("rb") else down

    monkeypatch.setattr(screener_mod, "list_all_master_symbols", fake_masters)
    monkeypatch.setattr(screener_mod, "get_kline_history", fake_kline)

    payload = await run_screener(
        None, "1d", [{"type": "macd", "op": "dif_above"}], trading_mode="live"
    )
    assert payload["scanned"] == 2
    assert payload["total"] == 1
    assert payload["items"][0]["symbol"] == "rb2610"
    assert "命中 1" in payload["message"]


@pytest.mark.asyncio
async def test_run_screener_requires_condition(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_masters(redis_conn: Any) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(screener_mod, "list_all_master_symbols", fake_masters)
    payload = await run_screener(None, "1d", [])
    assert payload["total"] == 0
    assert "至少" in payload["message"]
