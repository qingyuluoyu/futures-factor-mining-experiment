"""量化交易·决策模型（TypeSafe Jev System One）单测

- decision_client 纯函数：state 构建 / Choice 问题按持仓与多空标准动态构造 / 答案解析
- create_task 双向校验：decision 任务只收 jev 模型；ai 任务拒收 jev 模型
- decision_interval_sec：60-300 钳制
- 端到端（真实出网）：TYPESAFE_TEST_KEY 门控，见文件尾部
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.services.ai_trading.llm.decision_client import (
    build_questions,
    build_state,
    parse_action_answer,
)

UID = UUID("33333333-3333-3333-3333-333333333333")


def _task(**kw: Any) -> SimpleNamespace:
    base = dict(
        id=UID,
        user_id=UID,
        symbol="rb2610",
        symbol_name="螺纹钢",
        timeframe="5m",
        side_mode="both",
        model_row_id=UID,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _bar(i: int, close: float | None = None) -> dict[str, Any]:
    c = close if close is not None else 3000 + i
    return {
        "time": f"2026-09-21T09:{i:02d}:00",
        "open": c - 5,
        "high": c + 10,
        "low": c - 10,
        "close": c,
        "volume": 100 + i,
    }


# ---------- state 构建 ----------

def test_build_state_contains_market_bars_position_constraints() -> None:
    ctx = {
        "last_price": 3010.0,
        "position": {"direction": "long", "quantity": 2, "available_quantity": 2,
                     "avg_price": 3000.0, "unrealized_pnl": 200.0},
        "recent_bars": [_bar(i) for i in range(30)],
        "ai_bars_limit": 40,
        "hold_days": 1.5,
        "max_hold_days": 7,
        "ai_budget": 50000.0,
        "qty_min": 1,
        "qty_max": 3,
    }
    state = build_state(_task(), ctx)
    assert state["market"]["symbol"] == "rb2610"
    assert state["market"]["last_price"] == 3010.0
    assert len(state["recent_bars_ohlcv"]) == 30
    assert state["recent_bars_ohlcv"][0] == [
        "2026-09-21T09:00:00", 2995.0, 3010.0, 2990.0, 3000.0, 100.0,
    ]
    assert state["position"]["direction"] == "long"
    assert state["constraints"]["side_mode"] == "both"
    # 派生指标：30 根递增收盘 → ma_alignment bullish
    assert state["derived_indicators"]["ma_alignment"] == "bullish"
    assert state["derived_indicators"]["last_close"] == 3029.0


def test_build_state_flat_position_is_none() -> None:
    state = build_state(_task(), {"last_price": 1, "recent_bars": [], "position": None})
    assert state["position"] is None
    assert state["recent_bars_ohlcv"] == []
    assert state["derived_indicators"] == {}


# ---------- Choice 问题动态构造 ----------

def test_questions_no_position_both_sides() -> None:
    state = build_state(_task(side_mode="both"), {"position": None, "recent_bars": []})
    q = build_questions(state)
    crit = q["action"]["criteria"]
    assert set(crit.keys()) == {"hold", "open_long", "open_short"}
    assert q["action"]["type"] == "choice"


def test_questions_long_only_blocks_short() -> None:
    state = build_state(_task(side_mode="long_only"), {"position": None, "recent_bars": []})
    crit = build_questions(state)["action"]["criteria"]
    assert "open_long" in crit
    assert "open_short" not in crit


def test_questions_short_only_blocks_long() -> None:
    state = build_state(_task(side_mode="short_only"), {"position": None, "recent_bars": []})
    crit = build_questions(state)["action"]["criteria"]
    assert "open_short" in crit
    assert "open_long" not in crit


def test_questions_with_long_position_offers_close_and_reverse() -> None:
    state = build_state(
        _task(side_mode="both"),
        {"position": {"direction": "long", "quantity": 1}, "recent_bars": []},
    )
    crit = build_questions(state)["action"]["criteria"]
    assert "close_long" in crit
    assert "close_short" not in crit
    assert "open_short" in crit  # 反向可开（执行层先平后开语义）


def test_questions_with_short_position_long_only() -> None:
    """持空仓 + long_only：可平空/开多，不给开空"""
    state = build_state(
        _task(side_mode="long_only"),
        {"position": {"direction": "short", "quantity": 1}, "recent_bars": []},
    )
    crit = build_questions(state)["action"]["criteria"]
    assert "close_short" in crit
    assert "open_long" in crit
    assert "open_short" not in crit


# ---------- 答案解析 ----------

def test_parse_action_mappings() -> None:
    answers = {
        "action": {
            "type": "choice",
            "choice": "close_long",
            "confidence": 0.83,
            "probabilities": {"close_long": 0.83, "hold": 0.17},
        },
        "model": "jev-1.13.0",
    }
    out = parse_action_answer(answers)
    assert out["action"] == "close"
    assert out["quantity"] == 0  # 手数交 filter_ai_action 重算
    assert out["confidence"] == 0.83
    assert out["parse_ok"] is True
    assert out["jev_model"] == "jev-1.13.0"
    assert "Jev close_long→平仓" in out["reason"]


def test_parse_action_unknown_choice_falls_to_hold() -> None:
    out = parse_action_answer({"action": {"choice": "double_down", "confidence": 0.9}})
    assert out["action"] == "hold"


def test_parse_action_confidence_clamped() -> None:
    out = parse_action_answer({"action": {"choice": "open_long", "confidence": 7.5}})
    assert out["confidence"] == 1.0
    out2 = parse_action_answer({"action": {"choice": "open_long"}})
    assert out2["confidence"] == 0.0


def test_parse_action_empty_answers() -> None:
    out = parse_action_answer({})
    assert out["action"] == "hold"
    assert out["parse_ok"] is True


# ---------- create_task 双向校验（FakeSession） ----------

class _ExecResult:
    def __init__(self, first_row: Any) -> None:
        self._first = first_row

    def first(self):
        return self._first


class _FakeSession:
    """只支撑 _load_model_meta 的 select(AIModel, AIProvider).join 查询"""

    def __init__(self, model: Any, provider: Any) -> None:
        self._row = (model, provider)
        self.added: list[Any] = []

    async def execute(self, stmt):
        return _ExecResult(self._row)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


def _model_row(model_id: str = "jev-latest") -> SimpleNamespace:
    return SimpleNamespace(
        id=UID, user_id=UID, provider_id=UID, model_id=model_id,
        display_name="Jev", is_multimodal=False, is_custom=False,
        capabilities=["decision"],
    )


def _provider(api_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=UID, user_id=UID, name="TypeSafe Jev", api_type=api_type,
        base_url="https://api.typesafe.ai", api_key="enc", is_active=True,
        thinking_enabled=None,
    )


@pytest.fixture()
def _patch_create_deps(monkeypatch):
    """mock 账户/资金/时段等外部依赖，只留模型校验真跑"""
    async def _summary(session, user_id, mode="live"):
        return {"total_equity": 100000, "available_margin": 50000}

    async def _reserve(session, task, amount):
        return None

    def _status(symbol):
        return {"is_open": False}

    monkeypatch.setattr(
        "app.services.ai_trading.task.task_svc.get_account_summary", _summary
    )
    monkeypatch.setattr(
        "app.services.ai_trading.core.capital.reserve_task_capital", _reserve
    )
    monkeypatch.setattr(
        "app.services.trading_hours.get_session_status", _status
    )


async def test_create_decision_task_with_jev_model_ok(
    _patch_create_deps,
) -> None:
    from app.services.ai_trading.task.task_svc import create_task

    session = _FakeSession(_model_row(), _provider("jev"))
    body = {
        "strategy_type": "decision",
        "model_row_id": str(UID),
        "symbol": "rb2610",
        "timeframe": "5m",
        "side_mode": "both",
        "position_mode": "fixed_qty",
        "fixed_qty": 1,
        "decision_interval_sec": 180,
        "close_rules": {"ai_auto": True, "model_exit": True},
        "stop_rules": {"ai_auto": False},
        "auto_start": False,
    }
    data = await create_task(session, UID, body, "live")
    assert data["strategy_type"] == "decision"
    assert data["decision_interval_sec"] == 180
    # 模型自决平仓标记原样保留
    assert data["close_rules"].get("model_exit") is True
    assert data["close_rules"].get("ai_auto") is True


async def test_create_decision_task_rejects_non_jev_model(
    _patch_create_deps,
) -> None:
    from app.services.ai_trading.task.task_svc import create_task

    session = _FakeSession(_model_row("deepseek-chat"), _provider("openai"))
    with pytest.raises(HTTPException) as exc:
        await create_task(
            session, UID,
            {
                "strategy_type": "decision",
                "model_row_id": str(UID),
                "symbol": "rb2610",
                "timeframe": "5m",
                "auto_start": False,
            },
            "live",
        )
    assert exc.value.status_code == 400
    assert "Jev" in exc.value.detail


async def test_create_ai_task_rejects_jev_model(_patch_create_deps) -> None:
    """AI 交易里决策模型无法使用：后端强校验"""
    from app.services.ai_trading.task.task_svc import create_task

    session = _FakeSession(_model_row(), _provider("jev"))
    with pytest.raises(HTTPException) as exc:
        await create_task(
            session, UID,
            {
                "strategy_type": "ai",
                "model_row_id": str(UID),
                "symbol": "rb2610",
                "timeframe": "5m",
                "auto_start": False,
            },
            "live",
        )
    assert exc.value.status_code == 400
    assert "决策模型" in exc.value.detail


async def test_decision_interval_clamped(_patch_create_deps) -> None:
    from app.services.ai_trading.task.task_svc import create_task

    session = _FakeSession(_model_row(), _provider("jev"))
    data = await create_task(
        session, UID,
        {
            "strategy_type": "decision",
            "model_row_id": str(UID),
            "symbol": "rb2610",
            "timeframe": "1m",
            "decision_interval_sec": 10,  # 非法：低于 60
            "auto_start": False,
        },
        "live",
    )
    assert data["decision_interval_sec"] == 60


# ---------- 端到端：真实调用 Jev systemone 做交易决策 ----------
#
# 本地/CI 未设置 TYPESAFE_TEST_KEY 时跳过；Key 不落库、不进 Git。
# 运行：TYPESAFE_TEST_KEY=apikey_xxx pytest tests/test_decision_model_trading.py -k e2e


@pytest.mark.asyncio
async def test_e2e_jev_decision_call(monkeypatch):
    """build_state/build_questions 产出的载荷真实打到 /v1/systemone：
    验证 URL 拼接、认证、请求结构合法（非 422）、answer 可被 parse_action_answer 解析。"""
    import os

    api_key = os.environ.get("TYPESAFE_TEST_KEY")
    if not api_key:
        pytest.skip("未设置 TYPESAFE_TEST_KEY，跳过真实出网决策调用")

    import httpx

    from app.services.ai.providers.urls import resolve_endpoint
    from app.services.crypto import decrypt, encrypt

    task = _task()
    ctx = {
        "last_price": 3010.0,
        "position": None,
        "recent_bars": [_bar(i) for i in range(30)],
        "ai_bars_limit": 30,
        "hold_days": 0,
        "max_hold_days": 7,
        "ai_budget": 50000.0,
        "qty_min": 1,
        "qty_max": 2,
    }
    state = build_state(task, ctx)
    questions = build_questions(state)
    payload = {"state": state, "model": "jev-latest", "questions": questions}
    url = resolve_endpoint("https://api.typesafe.ai", "/systemone")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert body.get("model"), body
    out = parse_action_answer(body.get("answers") or {})
    assert out["action"] in ("hold", "open_long", "open_short", "close")
    assert 0.0 <= out["confidence"] <= 1.0
    # 加密-解密链路顺带验证（决策任务 key 存库前会 encrypt）
    assert decrypt(encrypt(api_key)) == api_key


# ---------- 混合模式：量化策略给决策模型喂信号 ----------

async def test_create_decision_task_with_strategy_envelope(
    _patch_create_deps,
) -> None:
    """decision_strategy 封装：kind+params 规范化入库，默认名含策略展示名"""
    from app.services.ai_trading.task.task_svc import create_task

    session = _FakeSession(_model_row(), _provider("jev"))
    data = await create_task(
        session, UID,
        {
            "strategy_type": "decision",
            "model_row_id": str(UID),
            "symbol": "rb2610",
            "timeframe": "5m",
            "decision_interval_sec": 120,
            "strategy_params": {
                "decision_strategy": {
                    "kind": "ma_cross",
                    "params": {"fast_period": 5, "slow_period": 20, "ma_type": "sma"},
                }
            },
            "auto_start": False,
        },
        "live",
    )
    ds = data["strategy_params"]["decision_strategy"]
    assert ds["kind"] == "ma_cross"
    assert ds["params"]["fast_period"] == 5
    assert ds["params"]["slow_period"] == 20
    # 默认任务名带策略展示段
    assert "MA5/20" in data["name"]


async def test_create_decision_task_rejects_bad_strategy_kind(
    _patch_create_deps,
) -> None:
    from app.services.ai_trading.task.task_svc import create_task

    session = _FakeSession(_model_row(), _provider("jev"))
    with pytest.raises(HTTPException) as exc:
        await create_task(
            session, UID,
            {
                "strategy_type": "decision",
                "model_row_id": str(UID),
                "symbol": "rb2610",
                "timeframe": "5m",
                "strategy_params": {
                    "decision_strategy": {"kind": "not_a_strategy", "params": {}}
                },
                "auto_start": False,
            },
            "live",
        )
    assert exc.value.status_code == 400


def test_build_state_includes_strategy_signal() -> None:
    """挂载策略的建议进入 state.strategy_signal，供 Jev 参考"""
    ctx = {
        "position": None,
        "recent_bars": [],
        "strategy_signal": {
            "kind": "ma_cross",
            "action": "open_long",
            "reason": "MA5 上穿 MA20",
            "confidence": 1.0,
        },
    }
    state = build_state(_task(), ctx)
    assert state["strategy_signal"]["suggested_action"] == "open_long"
    assert state["strategy_signal"]["kind"] == "ma_cross"


def test_build_state_without_strategy_signal_is_none() -> None:
    state = build_state(_task(), {"position": None, "recent_bars": []})
    assert state["strategy_signal"] is None


def test_questions_notes_reference_strategy_signal() -> None:
    """问题说明声明策略信号是参考、模型做最终判断"""
    state = build_state(_task(), {"position": None, "recent_bars": []})
    q = build_questions(state)
    notes = str(q["action"]["instructions"]["notes"])
    assert "strategy_signal" in notes
    assert "override" in notes


# ---------- 决策模型自主止损：state 带浮盈亏% ----------

def test_build_state_position_pnl_pct() -> None:
    """持仓带保证金与浮盈亏 → state 计算相对保证金百分比（模型止损依据）"""
    ctx = {
        "position": {
            "direction": "short", "quantity": 10, "available_quantity": 10,
            "avg_price": 5000.0, "unrealized_pnl": -1500.0, "margin": 30000.0,
        },
        "recent_bars": [],
    }
    state = build_state(_task(), ctx)
    assert state["position"]["unrealized_pnl_pct"] == -5.0


def test_build_state_position_no_margin_pct_none() -> None:
    ctx = {
        "position": {"direction": "long", "quantity": 1, "avg_price": 3000.0,
                     "unrealized_pnl": 100.0},
        "recent_bars": [],
    }
    state = build_state(_task(), ctx)
    assert state["position"]["unrealized_pnl_pct"] is None


def test_close_criteria_mention_stop_loss() -> None:
    """平仓判据含止损语义（cut a losing position）"""
    state = build_state(
        _task(), {"position": {"direction": "long", "quantity": 1}, "recent_bars": []}
    )
    crit = build_questions(state)["action"]["criteria"]
    assert "stopped out" in crit["close_long"]


# ---------- 因子开仓线可调 ----------

def _bars_for_factor(pos_sign: float) -> list[dict[str, Any]]:
    """构造 240 根单调行情让因子方向确定(简化:直接测阈值逻辑用 mock)"""
    return [_bar(i, close=3000 + i * pos_sign) for i in range(60)]


def test_factor_entry_threshold_default_unchanged() -> None:
    """未传 entry_threshold:归一化为默认 0.3,旧行为不变"""
    from app.services.ai_trading.strategies.factor import normalize_factor_params

    p = normalize_factor_params({"factor_tokens": [0, 8, 1, 14]})
    assert p["entry_threshold"] == 0.3


def test_factor_entry_threshold_clamped() -> None:
    from app.services.ai_trading.strategies.factor import normalize_factor_params

    assert normalize_factor_params(
        {"factor_tokens": [0, 8], "entry_threshold": 0.01}
    )["entry_threshold"] == 0.05
    assert normalize_factor_params(
        {"factor_tokens": [0, 8], "entry_threshold": 0.9}
    )["entry_threshold"] == 0.5
    assert normalize_factor_params(
        {"factor_tokens": [0, 8], "entry_threshold": "bad"}
    )["entry_threshold"] == 0.3


def test_factor_signal_respects_custom_threshold(monkeypatch) -> None:
    """自定义开仓线生效:pos=-0.22 在 0.3 线观望、在 0.2 线给开空建议"""
    from app.services.ai_trading.strategies import factor as fmod

    bars = _rising_bars() if False else None  # 用 mock 位置,不吃真行情
    for threshold, expect_open in ((0.3, False), (0.2, True)):
        params = {
            "factor_tokens": [0, 8, 1, 14],
            "factor_text": "t",
            "entry_threshold": threshold,
        }
        # 直接 mock 最新因子仓位为 -0.22
        monkeypatch.setattr(
            fmod, "_latest_position", lambda bars, tokens, weights=None: -0.22
        )
        sig = fmod.compute_factor_signal(
            [{"time": f"t{i}", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1} for i in range(30)],
            params, "both", None,
        )
        if expect_open:
            assert sig["action"] == "open_short", f"threshold={threshold}"
            assert "开空" in sig["reason"]
        else:
            assert sig["action"] == "hold", f"threshold={threshold}"
            assert "观望" in sig["reason"]


def test_factor_exit_decay_clamped_below_entry() -> None:
    """开仓线 0.2 时,decay 平仓阈值被钳到 <0.2 防开平互踩"""
    from app.services.ai_trading.core.exit_signals import normalize_factor_exit

    cfg = normalize_factor_exit(
        {"mode": "decay", "long_threshold": 0.25, "short_threshold": 0.28},
        entry_threshold=0.2,
    )
    assert cfg["long_threshold"] == 0.19
    assert cfg["short_threshold"] == 0.19


def test_factor_exit_reach_raised_above_entry() -> None:
    from app.services.ai_trading.core.exit_signals import normalize_factor_exit

    cfg = normalize_factor_exit(
        {"mode": "reach", "long_threshold": 0.36, "short_threshold": 0.4},
        entry_threshold=0.4,
    )
    assert cfg["long_threshold"] == 0.45
    assert cfg["short_threshold"] == 0.45


# ---------- 持仓时 hold 标签 = 持仓保持 ----------

def test_hold_label_position_aware() -> None:
    """无仓 hold=观望;持仓 hold=持仓保持(Jev 的 keep current exposure 语义)"""
    answers = {"action": {"choice": "hold", "confidence": 0.58,
                          "probabilities": {"hold": 0.72, "close_short": 0.27}}}
    out_flat = parse_action_answer(answers, has_position=False)
    assert "观望" in out_flat["reason"]
    out_pos = parse_action_answer(answers, has_position=True)
    assert "持仓保持" in out_pos["reason"]
    # 动作与置信度不受标签影响
    assert out_pos["action"] == "hold" and out_pos["confidence"] == 0.58


def test_questions_hold_criteria_covers_keep_exposure() -> None:
    """hold 判据本身含 keep current exposure 语义(模型侧无歧义)"""
    state = build_state(_task(), {"position": None, "recent_bars": []})
    crit = build_questions(state)["action"]["criteria"]
    assert "keep current exposure" in crit["hold"]
