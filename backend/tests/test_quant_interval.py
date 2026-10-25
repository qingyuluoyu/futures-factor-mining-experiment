"""量化任务快频评估间隔（quant_interval_sec）——全部量化策略

- normalize_quant_interval / _quant_fast_interval：钳制（0=收盘对齐；
  60 ≤ 间隔 ≤ K线周期秒数；未知周期/非量化策略回落 0）
- create_task：factor / ma_cross 均落库钳制
- 引擎门控：未到间隔 skip 不动 last_run_at（间隔锚点）；
  到期评估时 signal_bars 含未收盘 bar（bars[-1]），决策记录 bar_time 即其时间
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from app.models.ai_trading.task import AITradingTask
from app.services.ai_trading.engine import evaluate_task

UID = UUID("44444444-4444-4444-4444-444444444444")


# ---------- 纯函数：间隔钳制 ----------

def test_quant_fast_interval_clamp() -> None:
    from app.services.ai_trading.engine import _quant_fast_interval

    def t(sec: int, tf: str, stype: str = "factor") -> SimpleNamespace:
        return SimpleNamespace(
            quant_interval_sec=sec, timeframe=tf, strategy_type=stype
        )

    assert _quant_fast_interval(t(0, "5m")) == 0  # 0=未启用（按收盘对齐）
    assert _quant_fast_interval(t(None, "5m")) == 0
    assert _quant_fast_interval(t(30, "5m")) == 60  # 下限 1 分钟
    assert _quant_fast_interval(t(120, "5m")) == 120  # 区间内原样
    assert _quant_fast_interval(t(600, "5m")) == 300  # 上限=K线周期
    assert _quant_fast_interval(t(3600, "1d")) == 3600  # 日线允许更长
    assert _quant_fast_interval(t(600, "xx")) == 0  # 未知周期回落
    # 全部量化策略生效；decision / ai 不消费本字段
    assert _quant_fast_interval(t(120, "5m", "ma_cross")) == 120
    assert _quant_fast_interval(t(120, "5m", "band_swing")) == 120
    assert _quant_fast_interval(t(120, "5m", "decision")) == 0
    assert _quant_fast_interval(t(120, "5m", "ai")) == 0


def test_normalize_quant_interval_clamp() -> None:
    from app.services.ai_trading.task.task_svc import normalize_quant_interval

    assert normalize_quant_interval(None, "5m") == 0
    assert normalize_quant_interval(0, "5m") == 0
    assert normalize_quant_interval(30, "5m") == 60
    assert normalize_quant_interval(9999, "5m") == 300
    assert normalize_quant_interval("180", "15m") == 180
    assert normalize_quant_interval(3600, "xx") == 0


# ---------- create_task：落库钳制 ----------

class _FakeSession:
    """量化任务不走模型查询；仅支撑 add/flush"""

    def __init__(self) -> None:
        self.added: list[Any] = []

    async def execute(self, stmt):  # pragma: no cover - 未用到
        class _R:
            def scalars(self):
                return self

            def all(self):
                return []

        return _R()

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass


@pytest.fixture()
def _patch_create_deps(monkeypatch: pytest.MonkeyPatch):
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


def _quant_body(strategy: str, interval: int, params: dict) -> dict:
    return {
        "strategy_type": strategy,
        "symbol": "rb2610",
        "timeframe": "5m",
        "quant_interval_sec": interval,
        "strategy_params": params,
        "auto_start": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "strategy,params",
    [
        ("factor", {"factor_tokens": [0, 8, 1, 14]}),
        ("ma_cross", {"fast_period": 5, "slow_period": 20}),
        ("n_breakout", {"lookback": 20}),
    ],
)
async def test_create_quant_task_interval_clamped(
    _patch_create_deps, strategy: str, params: dict
) -> None:
    """全部量化策略（factor/均线/突破）间隔均按 [60, 周期秒数] 钳制落库"""
    from app.services.ai_trading.task.task_svc import create_task

    for raw, expect in ((30, 60), (600, 300), (0, 0), (120, 120)):
        session = _FakeSession()
        data = await create_task(
            session, UID, _quant_body(strategy, raw, params), "live"
        )
        assert data["quant_interval_sec"] == expect


# ---------- 引擎门控 ----------

def _make_quant_task(
    *,
    interval: int,
    last_run: datetime | None,
    strategy: str = "factor",
    timeframe: str = "5m",
) -> AITradingTask:
    params: dict = (
        {"factor_tokens": [0, 8, 1, 14]}
        if strategy == "factor"
        else {"fast_period": 5, "slow_period": 20}
    )
    return AITradingTask(
        id=uuid4(),
        user_id=UID,
        trading_mode="live",
        name="量化快频",
        model_row_id=None,
        strategy_type=strategy,
        strategy_params=params,
        icon=None,
        symbol="rb2610",
        symbol_name="螺纹钢2610",
        timeframe=timeframe,
        quant_interval_sec=interval,
        side_mode="both",
        position_mode="fixed_qty",
        fixed_qty=1,
        qty_min=1,
        qty_max=1,
        capital_usage_min_pct=Decimal("0"),
        capital_usage_max_pct=Decimal("100"),
        allocated_capital=Decimal("0"),
        risk_style="balanced",
        max_hold_days=10,
        custom_prompt_enabled=False,
        custom_prompt=None,
        position_opened_at=None,
        close_rules={},
        stop_rules={},
        status="running",
        pause_reason=None,
        last_run_at=last_run,
        last_bar_time=None,
        note=None,
    )


_BARS = [
    {"time": "2026-07-28 10:30:00", "close": 3500.0},
    {"time": "2026-07-28 10:31:00", "close": 3501.0},
    {"time": "2026-07-28 10:32:00", "close": 3502.0},
    {"time": "2026-07-28 10:33:00", "close": 3503.0},  # bars[-2]：已收盘
    {"time": "2026-07-28 10:34:00", "close": 3504.0},  # bars[-1]：未收盘 forming
]


@pytest.mark.asyncio
async def test_quant_interval_wait_skip_keeps_anchor() -> None:
    """未到间隔：skip 且不动 last_run_at（间隔锚点）"""
    now = datetime.now(timezone.utc)
    task = _make_quant_task(interval=300, last_run=now)
    session = AsyncMock()
    session.flush = AsyncMock()

    with (
        patch("app.services.ai_trading.engine.get_session_status") as m_sess,
        patch("app.services.ai_trading.engine.get_kline_history", new=AsyncMock()) as m_kline,
        patch("app.services.ai_trading.engine.resolve_last_price", new=AsyncMock(return_value=3500.0)),
        patch("app.services.ai_trading.engine.find_symbol_position", new=AsyncMock(return_value=None)),
        patch("app.services.ai_trading.engine.get_account_summary", new=AsyncMock(return_value={"total_equity": 100000})),
        patch("app.services.ai_trading.engine.check_hard_rules", return_value=None),
        patch("app.services.ai_trading.engine.snapshot_equity", new=AsyncMock()),
    ):
        m_sess.return_value = {"is_open": True}
        m_kline.return_value = _BARS

        result = await evaluate_task(session, task, redis_conn=None, force=False)

    assert result == {"skipped": True, "reason": "quant_interval_wait"}
    # 间隔锚点不动：下次仍按原 last_run_at 起算
    assert task.last_run_at == now


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["factor", "ma_cross"])
async def test_quant_fast_due_eval_uses_forming_bar(strategy: str) -> None:
    """到期评估：signal_bars 含未收盘 bar（bars[-1]），决策记录 bar_time 即其时间"""
    now = datetime.now(timezone.utc)
    task = _make_quant_task(
        interval=300, last_run=now - timedelta(minutes=10), strategy=strategy
    )
    session = AsyncMock()
    session.flush = AsyncMock()

    captured: dict[str, Any] = {}

    def fake_exit(task_, stype, signal_bars, position):
        captured["signal_bars"] = list(signal_bars)
        return {
            "action": "close",
            "quantity": 1,
            "reason": "测试出场",
            "trigger_type": "indicator_exit",
            "context": {},
        }

    with (
        patch("app.services.ai_trading.engine.get_session_status") as m_sess,
        patch("app.services.ai_trading.engine.get_kline_history", new=AsyncMock()) as m_kline,
        patch("app.services.ai_trading.engine.resolve_last_price", new=AsyncMock(return_value=3500.0)),
        patch("app.services.ai_trading.engine.find_symbol_position", new=AsyncMock(return_value=None)),
        patch("app.services.ai_trading.engine.get_account_summary", new=AsyncMock(return_value={"total_equity": 100000})),
        patch("app.services.ai_trading.engine.check_hard_rules", return_value=None),
        patch("app.services.ai_trading.engine._check_exit_standards", fake_exit),
        patch("app.services.ai_trading.engine.execute_action", new=AsyncMock(return_value=(uuid4(), "已平仓"))),
        patch("app.services.ai_trading.engine._apply_win_stats", new=AsyncMock()),
        patch("app.services.ai_trading.engine._write_decision", new=AsyncMock()) as m_dec,
        patch("app.services.ai_trading.engine.write_run_log", new=AsyncMock()),
        patch("app.services.ai_trading.engine.snapshot_equity", new=AsyncMock()),
    ):
        m_sess.return_value = {"is_open": True}
        m_kline.return_value = _BARS

        result = await evaluate_task(session, task, redis_conn=None, force=False)

    # 出场标准收到的序列末根 = 未收盘 bar（快频口径核心，全量化策略一致）
    assert captured["signal_bars"][-1]["time"] == _BARS[-1]["time"]
    assert len(captured["signal_bars"]) == len(_BARS)
    # 决策记录 bar_time = 未收盘 bar 时间
    assert m_dec.call_args.kwargs["bar_time"] == _BARS[-1]["time"]
    assert result["action"] == "close"


@pytest.mark.asyncio
async def test_quant_zero_interval_stays_bar_aligned() -> None:
    """interval=0：沿用按收盘对齐（should_run_for_bar），无新收盘 bar → no_new_bar"""
    task = _make_quant_task(interval=0, last_run=None)
    session = AsyncMock()
    session.flush = AsyncMock()

    with (
        patch("app.services.ai_trading.engine.get_session_status") as m_sess,
        patch("app.services.ai_trading.engine.get_kline_history", new=AsyncMock()) as m_kline,
        patch("app.services.ai_trading.engine.resolve_last_price", new=AsyncMock(return_value=3500.0)),
        patch("app.services.ai_trading.engine.find_symbol_position", new=AsyncMock(return_value=None)),
        patch("app.services.ai_trading.engine.get_account_summary", new=AsyncMock(return_value={"total_equity": 100000})),
        patch("app.services.ai_trading.engine.check_hard_rules", return_value=None),
        patch("app.services.ai_trading.engine.should_run_for_bar", return_value=(False, None)),
        patch("app.services.ai_trading.engine.snapshot_equity", new=AsyncMock()),
    ):
        m_sess.return_value = {"is_open": True}
        m_kline.return_value = _BARS

        result = await evaluate_task(session, task, redis_conn=None, force=False)

    assert result == {"skipped": True, "reason": "no_new_bar"}
