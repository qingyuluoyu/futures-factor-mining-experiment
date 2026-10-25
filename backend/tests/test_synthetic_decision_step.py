"""AI 生成 K 线回测 · 决策模型混合模式单测

step_decision 增设 decision_model_row_id：
- 量化策略信号 → 作为参考输入喂给 Jev（state.strategy_signal）
- 模型做最终投资判断（action/confidence），source="decision"
- normalize_action 把 quantity=0 规范为 fixed_qty，VirtualAccount 正确成交
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.services.backtest.synthetic import (
    _decision_signal_step,
    step_decision,
)

UID = UUID("44444444-4444-4444-4444-444444444444")


def _bar(i: int, close: float | None = None) -> dict[str, Any]:
    c = close if close is not None else 3000 + i
    return {
        "time": f"2026-09-21T09:{i:02d}:00",
        "open": c - 4,
        "high": c + 8,
        "low": c - 8,
        "close": c,
        "volume": 100 + i,
    }


def _rising_bars(n: int = 40) -> list[dict[str, Any]]:
    return [_bar(i) for i in range(n)]


@pytest.mark.asyncio
async def test_step_decision_uses_jev_and_marks_source(monkeypatch) -> None:
    """decision_model_row_id 设置时：走 Jev 分支、source=decision、
    quantity=0 被规范为 fixed_qty、账户正确开多"""
    captured: dict[str, Any] = {}

    async def _fake_jev(session, task, context):
        captured["task"] = task
        captured["context"] = context
        return {
            "action": "open_long",
            "quantity": 0,  # 交 normalize_action 按 fixed_qty 补
            "confidence": 0.9,
            "reason": "Jev open_long→开多 置信0.90",
            "parse_ok": True,
            "raw": {"choice": "open_long"},
        }

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fake_jev
    )

    result = await step_decision(
        session=None,
        user_id=UID,
        bars_so_far=_rising_bars(),
        account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both",
        fixed_qty=2,
        initial_cash=1_000_000,
        risk_style="balanced",
        custom_prompt_enabled=False,
        custom_prompt=None,
        close_rules={},
        stop_rules={},
        max_hold_days=10,
        model_row_id=None,
        symbol="rb2610",
        symbol_name="螺纹钢",
        timeframe="5m",
        decision_model_row_id=str(UID),
    )

    assert result["source"] == "decision"
    assert result["action"] == "open_long"
    assert result["quantity"] == 2  # fixed_qty 补位
    assert result["executed"] is True
    # 账户已开多
    assert result["account_snapshot"]["side"] == "long"
    assert result["account_snapshot"]["qty"] == 2
    # 传给 Jev 的 context 带策略信号（量化策略喂内容）
    sig = captured["context"].get("strategy_signal")
    assert sig is not None
    assert sig["kind"] == "ma_cross"
    assert sig["action"] in ("hold", "open_long", "open_short", "close")
    # 伪 task 携带决策模型绑定与品种信息
    assert str(captured["task"].model_row_id) == str(UID)
    assert captured["task"].symbol == "rb2610"


@pytest.mark.asyncio
async def test_step_decision_without_decision_model_keeps_quant(monkeypatch) -> None:
    """不设 decision_model_row_id：保持纯量化路径，不触碰决策模型"""
    called = False

    async def _fail_jev(session, task, context):
        nonlocal called
        called = True
        return {"action": "hold", "quantity": 0}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fail_jev
    )
    result = await step_decision(
        session=None,
        user_id=UID,
        bars_so_far=_rising_bars(),
        account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both",
        fixed_qty=1,
        initial_cash=1_000_000,
        risk_style="balanced",
        custom_prompt_enabled=False,
        custom_prompt=None,
        close_rules={},
        stop_rules={},
        max_hold_days=10,
        model_row_id=None,
        symbol="rb2610",
        symbol_name="螺纹钢",
        timeframe="5m",
        decision_model_row_id=None,
    )
    assert called is False
    assert result["source"] == "ma_cross"


@pytest.mark.asyncio
async def test_step_decision_jev_failure_falls_back_hold(monkeypatch) -> None:
    """决策模型异常：降级 hold，不中断回放"""

    async def _boom(session, task, context):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _boom
    )
    result = await step_decision(
        session=None,
        user_id=UID,
        bars_so_far=_rising_bars(),
        account_snapshot=None,
        strategy_type="n_breakout",
        strategy_params={"lookback": 10},
        side_mode="both",
        fixed_qty=1,
        initial_cash=1_000_000,
        risk_style="balanced",
        custom_prompt_enabled=False,
        custom_prompt=None,
        close_rules={},
        stop_rules={},
        max_hold_days=10,
        model_row_id=None,
        symbol="rb2610",
        symbol_name="",
        timeframe="5m",
        decision_model_row_id=str(UID),
    )
    assert result["source"] == "decision"
    assert result["action"] == "hold"
    assert result["executed"] is False
    assert "决策模型异常" in result["reason"]


@pytest.mark.asyncio
async def test_decision_signal_step_builds_context(monkeypatch) -> None:
    """_decision_signal_step 组装 context：持仓/预算/K线窗口/策略信号"""
    from app.services.backtest.account import VirtualAccount

    captured: dict[str, Any] = {}

    async def _fake_jev(session, task, context):
        captured.update(context)
        return {"action": "hold", "quantity": 0, "confidence": 0.5,
                "reason": "Jev hold", "parse_ok": True}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fake_jev
    )
    account = VirtualAccount(
        cash=800000, multiplier=10, margin_rate=0.1,
        fee_mode="rate", open_fee=0.0001, close_fee=0.0001,
    )
    out = await _decision_signal_step(
        None, UID, str(UID),
        bars_so_far=_rising_bars(50),
        pos=None, price=3040.0,
        symbol="rb2610", symbol_name="螺纹钢", timeframe="5m",
        side_mode="both", fixed_qty=2,
        hold_days=None, max_hold_days=10,
        account=account,
        strategy_kind="macd_cross",
        quant_action="open_short",
        quant_reason="MACD 死叉",
    )
    assert out["action"] == "hold"
    # K 线窗口裁到 40 根
    assert len(captured["recent_bars"]) == 40
    assert captured["ai_bars_limit"] == 40
    assert captured["strategy_signal"]["kind"] == "macd_cross"
    assert captured["strategy_signal"]["action"] == "open_short"
    assert captured["ai_budget"] == 800000
    assert captured["qty_min"] == 2


# ---------- 端到端：真实 Jev 出网（FakeSession 提供 jev 渠道行） ----------


class _ModelProviderResult:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row


class _JevSession:
    """只支撑 call_jev_decision 的 select(AIModel, AIProvider).join 查询"""

    def __init__(self) -> None:
        from types import SimpleNamespace

        model = SimpleNamespace(
            id=UID, user_id=UID, model_id="jev-latest", display_name="Jev",
        )
        provider = SimpleNamespace(
            id=UID, name="TypeSafe Jev", api_type="jev",
            base_url="https://api.typesafe.ai",
            api_key=None,  # 在 fixture 里按需注入密文
        )
        self._row = (model, provider)

    async def execute(self, stmt):
        return _ModelProviderResult(self._row)


@pytest.mark.asyncio
async def test_e2e_step_decision_real_jev() -> None:
    """真实出网：完整 step_decision（决策分支）打 /v1/systemone 并返回可执行动作"""
    import os

    api_key = os.environ.get("TYPESAFE_TEST_KEY")
    if not api_key:
        pytest.skip("未设置 TYPESAFE_TEST_KEY，跳过真实出网 step 决策")

    from app.services.crypto import encrypt

    session = _JevSession()
    session._row[1].api_key = encrypt(api_key)
    result = await step_decision(
        session=session,
        user_id=UID,
        bars_so_far=_rising_bars(40),
        account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both",
        fixed_qty=1,
        initial_cash=1_000_000,
        risk_style="balanced",
        custom_prompt_enabled=False,
        custom_prompt=None,
        close_rules={},
        stop_rules={},
        max_hold_days=10,
        model_row_id=None,
        symbol="rb2610",
        symbol_name="螺纹钢",
        timeframe="5m",
        decision_model_row_id=str(UID),
    )
    assert result["source"] == "decision"
    assert result["action"] in ("hold", "open_long", "open_short", "close")
    assert "Jev" in result["reason"] or result["action"] == "hold"


# ---------- 决策模型调用限频（间隔=回放速度 2-5s） ----------

@pytest.mark.asyncio
async def test_decision_throttled_uses_quant_signal(monkeypatch) -> None:
    """距上次调用不足间隔：不打 Jev，降级策略信号并提示限频"""
    called = False

    async def _fail_jev(session, task, context):
        nonlocal called
        called = True
        return {"action": "hold", "quantity": 0}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fail_jev
    )
    import time as _time

    now_ms = _time.time() * 1000
    result = await step_decision(
        session=None, user_id=UID,
        bars_so_far=_rising_bars(),
        account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
        decision_min_interval_ms=3000,
        last_decision_at=now_ms - 500,  # 0.5s 前刚调用 → 限频
    )
    assert called is False
    assert result["source"] == "ma_cross"  # 策略信号直出
    assert "限频" in result["reason"]
    # 锚点原样回传（保持限频窗口）
    assert result["decision_called_at"] == pytest.approx(now_ms - 500, abs=1)


@pytest.mark.asyncio
async def test_decision_interval_elapsed_calls_jev(monkeypatch) -> None:
    """距上次调用超过间隔：正常调用 Jev，回传新锚点"""

    async def _fake_jev(session, task, context):
        return {"action": "open_long", "quantity": 0, "confidence": 0.9,
                "reason": "Jev open_long→开多", "parse_ok": True}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fake_jev
    )
    import time as _time

    now_ms = _time.time() * 1000
    result = await step_decision(
        session=None, user_id=UID,
        bars_so_far=_rising_bars(),
        account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
        decision_min_interval_ms=2000,
        last_decision_at=now_ms - 2500,  # 2.5s 前 → 已过间隔
    )
    assert result["source"] == "decision"
    assert result["action"] == "open_long"
    # 新锚点≈当前时刻
    assert result["decision_called_at"] == pytest.approx(now_ms, abs=2000)


@pytest.mark.asyncio
async def test_decision_interval_clamped_to_2000(monkeypatch) -> None:
    """间隔参数钳制：传 10ms 也按 2000ms 生效（1s 前的锚点仍限频）"""
    called = False

    async def _fail_jev(session, task, context):
        nonlocal called
        called = True
        return {"action": "hold", "quantity": 0}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fail_jev
    )
    import time as _time

    now_ms = _time.time() * 1000
    result = await step_decision(
        session=None, user_id=UID,
        bars_so_far=_rising_bars(),
        account_snapshot=None,
        strategy_type="n_breakout",
        strategy_params={"lookback": 10},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
        decision_min_interval_ms=10,  # 非法小值 → 钳到 2000
        last_decision_at=now_ms - 1000,  # 1s 前 < 2000ms → 仍限频
    )
    assert called is False
    assert "限频" in result["reason"]


# ---------- 决策模型自主止损（回测） ----------

@pytest.mark.asyncio
async def test_step_decision_stop_self_off_blocks_close(monkeypatch) -> None:
    """stop_rules.ai_auto=False：拦截决策模型与降级策略的平仓"""

    async def _fake_jev(session, task, context):
        return {"action": "close", "quantity": 0, "confidence": 0.8,
                "reason": "Jev close_long→平仓", "parse_ok": True}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fake_jev
    )
    # 先开一仓
    open_snap = None
    result = None
    bars = _rising_bars()
    # 手动构造持多快照：先用开仓 step
    async def _open_jev(session, task, context):
        return {"action": "open_long", "quantity": 0, "confidence": 0.9,
                "reason": "Jev open_long→开多", "parse_ok": True}
    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _open_jev
    )
    opened = await step_decision(
        session=None, user_id=UID, bars_so_far=bars, account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={"ai_auto": True}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
    )
    assert opened["executed"] is True

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _fake_jev
    )
    result = await step_decision(
        session=None, user_id=UID, bars_so_far=bars, account_snapshot=opened["account_snapshot"],
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={"ai_auto": False}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
    )
    assert result["action"] == "hold"
    assert "自主止损" in result["reason"]
    assert result["executed"] is False
    # 仓位未平
    assert result["account_snapshot"]["side"] == "long"


@pytest.mark.asyncio
async def test_step_decision_stop_self_on_allows_close(monkeypatch) -> None:
    """stop_rules.ai_auto=True（或缺省）：模型平仓放行"""

    async def _open_jev(session, task, context):
        return {"action": "open_long", "quantity": 0, "confidence": 0.9,
                "reason": "Jev open_long→开多", "parse_ok": True}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _open_jev
    )
    opened = await step_decision(
        session=None, user_id=UID, bars_so_far=_rising_bars(), account_snapshot=None,
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={"ai_auto": True}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
    )
    assert opened["executed"] is True

    async def _close_jev(session, task, context):
        # 验证 state 持仓带浮盈亏/保证金（止损判断依据）
        pos = context.get("position") or {}
        assert "unrealized_pnl" in pos and "margin" in pos
        return {"action": "close", "quantity": 0, "confidence": 0.8,
                "reason": "Jev close_long→平仓", "parse_ok": True}

    monkeypatch.setattr(
        "app.services.ai_trading.llm.decision_client.call_jev_decision", _close_jev
    )
    result = await step_decision(
        session=None, user_id=UID, bars_so_far=_rising_bars(),
        account_snapshot=opened["account_snapshot"],
        strategy_type="ma_cross",
        strategy_params={"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
        side_mode="both", fixed_qty=1, initial_cash=1_000_000,
        risk_style="balanced", custom_prompt_enabled=False, custom_prompt=None,
        close_rules={}, stop_rules={"ai_auto": True}, max_hold_days=10,
        model_row_id=None, symbol="rb2610", symbol_name="", timeframe="5m",
        decision_model_row_id=str(UID),
    )
    assert result["action"] == "close"
    assert result["executed"] is True
    assert result["account_snapshot"]["side"] == "flat"
