"""AI 交易多周期共振 + 每周期 K 线根数 单测

- extra_timeframes：主周期外最多 3 个附加周期（总周期 ≤ 4），去重/剔主周期/非法值报 400
- ai_bars_limit：AI 每周期查看根数，默认 40，夹到 [10, 240]
- prompt 压缩按 ai_bars_limit 截断（默认回落 12）
- 决策日志 bars 瘦身为根数摘要
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.services.ai_trading.llm.prompts import (
    _compact_context,
    build_system_prompt,
)
from app.services.ai_trading.task.task_svc import (
    normalize_ai_bars_limit,
    normalize_extra_timeframes,
)


# ---------- extra_timeframes 校验 ----------

def test_extras_dedup_and_skip_main() -> None:
    """重复值与主周期自动剔除，保序"""
    assert normalize_extra_timeframes("5m", ["15m", "15m", "5m", "60m"]) == ["15m", "60m"]


def test_extras_none_and_empty() -> None:
    assert normalize_extra_timeframes("5m", None) == []
    assert normalize_extra_timeframes("5m", []) == []


def test_extras_invalid_timeframe_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        normalize_extra_timeframes("5m", ["2h"])
    assert exc.value.status_code == 400


def test_extras_over_limit_rejected() -> None:
    """附加周期最多 3 个（加主周期共 4 个）"""
    with pytest.raises(HTTPException) as exc:
        normalize_extra_timeframes("1m", ["5m", "15m", "30m", "60m"])
    assert exc.value.status_code == 400


def test_extras_max_three_ok() -> None:
    assert normalize_extra_timeframes("1m", ["5m", "15m", "30m"]) == ["5m", "15m", "30m"]


def test_extras_non_list_rejected() -> None:
    with pytest.raises(HTTPException):
        normalize_extra_timeframes("5m", "15m")


# ---------- ai_bars_limit 校验 ----------

def test_bars_limit_default_40() -> None:
    assert normalize_ai_bars_limit(None) == 40


def test_bars_limit_clamped() -> None:
    """超出 [10, 240] 夹回边界"""
    assert normalize_ai_bars_limit(5) == 10
    assert normalize_ai_bars_limit(999) == 240
    assert normalize_ai_bars_limit("88") == 88
    assert normalize_ai_bars_limit("bad") == 40


# ---------- prompt 压缩 ----------

def _bar(i: int) -> dict[str, Any]:
    return {
        "time": f"2026-09-03T09:{i:02d}:00",
        "open": 3000 + i,
        "high": 3010 + i,
        "low": 2990 + i,
        "close": 3005 + i,
        "volume": 100 + i,
        "extra_field": "should_be_dropped",
    }


def test_compact_trims_to_ai_bars_limit() -> None:
    """ai_bars_limit=40：40 根截断 + 字段精简为 t/o/h/l/c/v"""
    ctx = {"ai_bars_limit": 40, "recent_bars": [_bar(i) for i in range(60)]}
    out = _compact_context(ctx)
    bars = out["recent_bars"]
    assert len(bars) == 40
    assert bars[-1]["t"] == _bar(59)["time"]
    assert set(bars[0].keys()) == {"t", "o", "h", "l", "c", "v"}


def test_compact_default_12_without_limit() -> None:
    """未配置 ai_bars_limit：回落旧默认 12 根"""
    ctx = {"recent_bars": [_bar(i) for i in range(30)]}
    out = _compact_context(ctx)
    assert len(out["recent_bars"]) == 12


def test_compact_extra_bars_per_timeframe() -> None:
    """extra_bars 各周期独立截断精简"""
    ctx = {
        "ai_bars_limit": 20,
        "recent_bars": [_bar(i) for i in range(25)],
        "extra_bars": {
            "30m": [_bar(i) for i in range(25)],
            "1d": [_bar(i) for i in range(10)],
        },
    }
    out = _compact_context(ctx)
    assert len(out["recent_bars"]) == 20
    assert len(out["extra_bars"]["30m"]) == 20
    assert len(out["extra_bars"]["1d"]) == 10
    assert set(out["extra_bars"]["30m"][0].keys()) == {"t", "o", "h", "l", "c", "v"}


# ---------- system prompt 多周期段 ----------

def _sys_prompt(extras: list[str] | None) -> str:
    return build_system_prompt(
        risk_style="balanced",
        timeframe="5m",
        max_hold_days=7,
        horizon="短线",
        allocated_capital=100000.0,
        custom_prompt_enabled=False,
        custom_prompt=None,
        has_factor_signal=False,
        extra_timeframes=extras,
    )


def test_system_prompt_has_multitf_section() -> None:
    prompt = _sys_prompt(["30m", "1d"])
    assert "多周期共振" in prompt
    assert "30m、1d" in prompt
    assert "观望" in prompt


def test_system_prompt_no_multitf_section_by_default() -> None:
    prompt = _sys_prompt(None)
    assert "多周期共振" not in prompt


# ---------- 决策日志 bars 瘦身 ----------

class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_write_decision_slims_bars() -> None:
    """决策日志入库的 context_summary 不含原始 K 线，只留根数摘要"""
    from app.services.ai_trading.engine import _write_decision

    task = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        timeframe="5m",
    )
    ctx = {
        "recent_bars": [_bar(i) for i in range(40)],
        "extra_bars": {"30m": [_bar(i) for i in range(40)], "1d": [_bar(i) for i in range(20)]},
        "last_price": 3000.0,
    }
    session = _FakeSession()
    row = await _write_decision(
        session,  # type: ignore[arg-type]
        task,  # type: ignore[arg-type]
        bar_time="2026-09-03 09:30:00",
        trigger_type="bar",
        context=ctx,
        model_output={"action": "hold"},
        action="hold",
        order_id=None,
        reason="观望",
    )
    summary = row.context_summary
    assert summary["recent_bars"] == {"count": 40}
    assert summary["extra_bars"] == {"30m": {"count": 40}, "1d": {"count": 20}}
    # 原始 context 不被修改（模型侧仍拿完整 K 线）
    assert len(ctx["recent_bars"]) == 40
    assert summary["last_price"] == 3000.0
