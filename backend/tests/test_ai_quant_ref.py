"""AI 决策多选量化策略参考信号测试（quant_ref 模块 + 持久化规范化 + prompt 注入）"""

from __future__ import annotations

from app.services.ai_trading.strategies.quant_ref import (
    QUANT_REF_KINDS,
    build_quant_ref_signals,
    normalize_ref_strategies,
    quant_ref_snapshot,
)
from app.services.ai_trading.task.task_svc import _normalize_ai_strategy_params


def _bars_up(n: int = 80, start: float = 100.0) -> list[dict]:
    """单调上涨收盘序列（各策略均可计算）"""
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


def test_quant_ref_snapshot_each_kind() -> None:
    """白名单内每个策略都能产出 AI 参考快照（结构完整）"""
    bars = _bars_up()
    for kind in QUANT_REF_KINDS:
        snap = quant_ref_snapshot(kind, bars)
        assert snap is not None, f"{kind} 应产出快照"
        assert snap["kind"] == kind
        assert snap["action"] in ("hold", "open_long", "open_short", "close")
        assert isinstance(snap["signal"], str)
        assert isinstance(snap["reason"], str)
        assert snap["close"] > 0
        assert "detail" in snap


def test_quant_ref_snapshot_unknown_or_empty() -> None:
    """非法 kind / factor / 空 bars 返回 None（静默跳过）"""
    assert quant_ref_snapshot("factor", _bars_up()) is None
    assert quant_ref_snapshot("bogus", _bars_up()) is None
    assert quant_ref_snapshot("", _bars_up()) is None
    assert quant_ref_snapshot("macd_cross", []) is None


def test_build_quant_ref_signals() -> None:
    """多选列表 → 参考信号列表：去重、非法 kind 跳过、保持选中顺序"""
    sigs = build_quant_ref_signals(
        [
            {"kind": "macd_cross", "params": None},
            {"kind": "kdj_cross"},
            {"kind": "bogus"},
            {"kind": "macd_cross"},  # 重复应被去重
        ],
        _bars_up(),
    )
    assert [s["kind"] for s in sigs] == ["macd_cross", "kdj_cross"]
    assert build_quant_ref_signals(None, _bars_up()) == []
    assert build_quant_ref_signals([], _bars_up()) == []
    assert build_quant_ref_signals([{"kind": "macd_cross"}], []) == []


def test_normalize_ref_strategies() -> None:
    """校验：factor 不在白名单、去重、params 仅保留 dict、非列表拒绝"""
    out = normalize_ref_strategies(
        [
            {"kind": "factor"},
            {"kind": "macd_cross", "params": {"fast_period": 5}},
            "junk",
            {"kind": "macd_cross", "params": None},
        ]
    )
    assert out == [{"kind": "macd_cross", "params": {"fast_period": 5}}]
    assert normalize_ref_strategies("junk") == []
    assert normalize_ref_strategies([{"kind": "kdj_cross", "params": [1, 2]}]) == [
        {"kind": "kdj_cross", "params": None}
    ]


def test_normalize_ai_strategy_params() -> None:
    """AI 任务 strategy_params：factor_tokens 洗成 int 列表 + ref_strategies 白名单"""
    p = _normalize_ai_strategy_params(
        {
            "factor_tokens": [1, "2", 3.5, "x"],
            "ref_strategies": [{"kind": "kdj_cross"}, {"kind": "factor"}],
            "extra": 1,
        }
    )
    assert p == {
        "factor_tokens": [1, 2, 3],
        "ref_strategies": [{"kind": "kdj_cross", "params": None}],
    }
    assert _normalize_ai_strategy_params(None) == {}
    assert _normalize_ai_strategy_params({"factor_tokens": "junk"}) == {}


def test_system_prompt_quant_ref_section() -> None:
    """勾选量化策略参考时 system prompt 才拼入 QUANT_REF_SIGNALS_SECTION"""
    from app.services.ai_trading.llm.prompts import build_system_prompt

    base = {
        "risk_style": "balanced",
        "timeframe": "1d",
        "max_hold_days": 10,
        "horizon": "日线",
        "allocated_capital": 100000.0,
        "custom_prompt_enabled": False,
        "custom_prompt": None,
        "has_factor_signal": False,
    }
    plain = build_system_prompt(**base)
    assert "quant_ref_signals" not in plain
    with_ref = build_system_prompt(**base, has_quant_ref_signals=True)
    assert "quant_ref_signals" in with_ref
    assert "量化策略参考" in with_ref
