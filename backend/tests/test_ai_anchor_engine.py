"""AI 看盘主播引擎语义测试 —— mock 数据源/模型，验证播报闭环

覆盖：闭盘顺延不播报、数据不足降级、正常播报落库+WS 推送+间隔推进、
模型失败降级观望、上下文历史注入 prompt。
"""

from __future__ import annotations

import operator
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.models.ai_anchor import AIAnchorBroadcast, AIAnchorTask
from app.services.ai_anchor.engine import evaluate_anchor
from app.services.ai_anchor.svc import utc_now


def _make_task(**overrides: Any) -> AIAnchorTask:
    task = AIAnchorTask(
        user_id=uuid4(),
        trading_mode="virtual",
        model_row_id=uuid4(),
        symbol="rb2610",
        timeframe="15m",
        bar_count=60,
        interval_minutes=15,
        indicators=[{"name": "MACD", "params": {}}, {"name": "MA", "params": {"period": 20}}],
        status="running",
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


def _make_bars(count: int) -> list[dict[str, Any]]:
    """合成温和上行K线（时间单调递增唯一，含指标计算所需 OHLCV 字段）。"""
    bars: list[dict[str, Any]] = []
    base_time = datetime(2026, 8, 25, 9, 0, 0)
    base = 3200.0
    for i in range(count):
        close = base + i * 2.5
        bars.append(
            {
                "time": (base_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                "open": round(close - 2.0, 2),
                "high": round(close + 3.0, 2),
                "low": round(close - 4.0, 2),
                "close": round(close, 2),
                "volume": 1000 + i * 10,
            }
        )
    return bars


class _Scalars(list):
    def all(self) -> "_Scalars":
        return self


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if len(self._rows) == 1 else None

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)


class FakeSession:
    """内存版 AsyncSession：仅支持 select(AIAnchorBroadcast)（上下文历史查询）"""

    def __init__(self, broadcasts: list[AIAnchorBroadcast] | None = None) -> None:
        self.broadcasts: list[AIAnchorBroadcast] = list(broadcasts or [])
        self._pending: list[Any] = []
        self.pushed: list[dict[str, Any]] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        await self.flush()
        entity = stmt.column_descriptions[0]["entity"]
        if entity is not AIAnchorBroadcast:
            raise AssertionError(f"FakeSession 不支持 {entity}")
        rows = list(self.broadcasts)
        # 只做宽松过滤：task_id/symbol/timeframe/model_error 等值匹配（值非简单类型时跳过）
        wc = getattr(stmt, "whereclause", None)
        if wc is not None:
            for clause in getattr(wc, "clauses", [wc]):
                right = clause.right
                # is_(False)/is_(True) 常量元素（False_/True_）无 .value，按类型名识别
                if type(right).__name__ in ("False_", "True_"):
                    value: Any = type(right).__name__ == "True_"
                else:
                    value = getattr(right, "value", right)
                # created_at >= datetime（上下文每日 20:00 重置边界）
                if (
                    getattr(clause, "operator", None) is operator.ge
                    and isinstance(value, datetime)
                ):
                    rows = [
                        r
                        for r in rows
                        if (getattr(r, clause.left.key) or utc_now()) >= value
                    ]
                    continue
                if isinstance(value, (str, bool, int, float, uuid.UUID)):
                    rows = [r for r in rows if getattr(r, clause.left.key) == value]
        rows.sort(key=lambda r: r.created_at or utc_now(), reverse=True)
        limit = getattr(stmt, "_limit", None)
        return _FakeResult(rows[:limit] if limit else rows)

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    async def flush(self) -> None:
        for obj in self._pending:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            self.broadcasts.append(obj)
        self._pending.clear()

    async def commit(self) -> None:
        await self.flush()

    async def rollback(self) -> None:
        self._pending.clear()


def _prior_broadcast(task: AIAnchorTask, commentary: str) -> AIAnchorBroadcast:
    """历史播报（成功、同品种同周期，用于上下文注入）。

    created_at 锚定到"当前上下文边界（北京时间非周末 20:00）之后 1 分钟"，
    而非 now-30min——后者在每天 20:00~20:30 之间会跨过边界被
    build_context_history 排除，prompt 变"首次播报"，断言失败
    （2026-09-01 20:07 该时间炸弹卡住 33 自动部署即此）。
    """
    from app.services.ai_anchor.svc import context_boundary_utc

    return AIAnchorBroadcast(
        task_id=task.id,
        user_id=task.user_id,
        symbol=task.symbol,
        timeframe=task.timeframe,
        direction="long",
        entry=3210.0,
        take_profit=3260.0,
        stop_loss=3180.0,
        action="trade",
        commentary=commentary,
        model_error=False,
        created_at=context_boundary_utc(utc_now()) + timedelta(minutes=1),
    )


_MODEL_JSON = (
    '{"direction":"long","entry":3215.0,"take_profit":3260.0,'
    '"stop_loss":3190.0,"action":"trade","commentary":"均线多头排列，MACD 金叉，逢低做多。",'
    '"key_levels":[{"price":3262.0,"type":"resistance","label":"前高压力"},'
    '{"price":3185.0,"type":"support","label":"缺口支撑"},'
    '{"price":"bad","type":"support","label":"无效"}]}'
)


async def test_evaluate_closed_market_skips():
    """闭盘（live 非交易时段）：顺延一个间隔，不落播报"""
    task = _make_task(trading_mode="live")
    session = FakeSession()
    before = utc_now()
    with patch(
        "app.services.ai_anchor.engine.is_symbol_trading", return_value=False
    ), patch(
        "app.services.ai_anchor.engine.get_kline_history", new_callable=AsyncMock
    ) as mock_kline, patch(
        "app.api.ws.broadcast_anchor"
    ) as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)
    assert result == {"skipped": "market_closed"}
    mock_kline.assert_not_awaited()
    mock_push.assert_not_called()
    assert session.broadcasts == []
    assert task.next_run_at is not None and task.next_run_at > before
    assert task.last_run_at is None


async def test_evaluate_insufficient_bars_degrades():
    """K线数据不足：降级观望播报（model_error），间隔照常推进"""
    task = _make_task()
    session = FakeSession()
    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(5),
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)
    assert result["broadcast"] is True and result["model_error"] is True
    row = session.broadcasts[-1]
    assert row.action == "wait"
    assert row.model_error is True
    assert "数据不足" in row.commentary
    mock_push.assert_called_once()
    assert task.next_run_at is not None


async def test_evaluate_happy_path():
    """正常播报：落库 + WS 推送 + next_run_at 按间隔推进 + prompt 含指标与历史"""
    task = _make_task()
    prior = _prior_broadcast(task, "此前观点：多头结构完好，回调做多。")
    session = FakeSession(broadcasts=[prior])
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        before = utc_now()
        result = await evaluate_anchor(session, task, redis_conn=None)

    assert result["broadcast"] is True and result["model_error"] is False
    row = session.broadcasts[-1]
    assert row.direction == "long"
    assert row.action == "trade"
    assert row.entry == 3215.0
    assert row.take_profit == 3260.0
    assert row.stop_loss == 3190.0
    assert "逢低做多" in row.commentary
    # 关键价位解析：有效两条入库，price 非法的被丢弃
    assert [
        (item["price"], item["type"]) for item in (row.key_levels or [])
    ] == [(3262.0, "resistance"), (3185.0, "support")]
    # 间隔推进：next_run_at ≈ now + interval_minutes（容忍 ±2s 误差）
    expected = before + timedelta(minutes=task.interval_minutes)
    assert abs((task.next_run_at - expected).total_seconds()) < 5
    assert task.last_run_at is not None
    # prompt：K线窗口只含最新 60 根（起点为最新一根）+ 指标摘要 + 历史播报
    user_prompt = captured["messages"][1]["content"]
    assert "品种：rb2610" in user_prompt
    assert "分时K线段：15m" in user_prompt
    assert "分析窗口：每时段最新 60 根" in user_prompt
    assert "【时段1：15m（主时段）" in user_prompt
    assert '"name": "MACD"' in user_prompt
    assert "多头结构完好" in user_prompt
    # 模型只看 bar_count 根：第 60 根之前的K线时间不出现在时段1 K线块
    tf_block = user_prompt.split("【时段1")[1].split("【你的最近播报记录")[0]
    assert "09:00:00" not in tf_block
    # WS 推送 payload 自包含（含关键价位）
    mock_push.assert_called_once()
    payload = mock_push.call_args.args[1]
    assert payload["trading_mode"] == "virtual"
    assert payload["broadcast"]["direction"] == "long"
    assert payload["broadcast"]["key_levels"][0]["price"] == 3262.0
    assert payload["next_run_at"] is not None


async def test_evaluate_direction_mode_constrains_prompt():
    """方向偏好注入 system prompt：只做多时明确禁止做空建议"""
    task = _make_task(direction_mode="long")
    session = FakeSession()
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor"):
        await evaluate_anchor(session, task, redis_conn=None)

    system_prompt = captured["messages"][0]["content"]
    assert "只看做多方向" in system_prompt
    assert "禁止给出做空建议" in system_prompt


async def test_evaluate_model_failure_degrades():
    """模型调用抛错：降级观望播报（model_error），任务不死、间隔推进"""
    task = _make_task()
    session = FakeSession()
    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once",
        new_callable=AsyncMock,
        side_effect=ValueError("模型HTTP429"),
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)
    assert result["model_error"] is True
    row = session.broadcasts[-1]
    assert "模型调用失败" in row.commentary
    assert row.action == "wait"
    mock_push.assert_called_once()
    assert task.next_run_at is not None


async def test_context_excludes_error_and_mismatched_rows():
    """上下文过滤：错误播报与换品种的旧播报不注入 prompt

    （多时段改造后不再按周期过滤——调整时段组合后观点保持连贯）
    """
    task = _make_task()
    error_row = _prior_broadcast(task, "模型调用失败：HTTP500")
    error_row.model_error = True
    other_symbol = _prior_broadcast(task, "别的品种观点")
    other_symbol.symbol = "hc2610"
    other_tf = _prior_broadcast(task, "换时段前的旧观点")
    other_tf.timeframe = "5m"
    matched = _prior_broadcast(task, "同品种的旧观点")
    session = FakeSession(broadcasts=[error_row, other_symbol, other_tf, matched])
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor"):
        from app.services.ai_anchor.svc import build_context_history

        history = await build_context_history(session, task)
    assert {item["commentary"] for item in history} == {
        "换时段前的旧观点",
        "同品种的旧观点",
    }


def test_context_boundary_daily_reset():
    """上下文边界：北京时间 20:00 重置；周末不清空（跨周末保留到周一 20:00）"""
    from app.services.ai_anchor.svc import context_boundary_utc

    def utc(y: int, mo: int, d: int, h: int) -> datetime:
        return datetime(y, mo, d, h, 0, 0, tzinfo=timezone.utc)

    def bj2000(y: int, mo: int, d: int) -> datetime:
        # 北京 20:00 = UTC 12:00
        return datetime(y, mo, d, 12, 0, 0, tzinfo=timezone.utc)

    # 周三白天（北京 10:00）→ 周二 20:00
    assert context_boundary_utc(utc(2026, 8, 26, 2)) == bj2000(2026, 8, 25)
    # 周三夜里（北京 21:00）→ 当天 20:00
    assert context_boundary_utc(utc(2026, 8, 26, 13)) == bj2000(2026, 8, 26)
    # 周六白天 → 周五 20:00
    assert context_boundary_utc(utc(2026, 8, 29, 3)) == bj2000(2026, 8, 28)
    # 周六夜里（北京 21:00）→ 仍周五 20:00（周末不清空）
    assert context_boundary_utc(utc(2026, 8, 29, 13)) == bj2000(2026, 8, 28)
    # 周日 → 周五 20:00
    assert context_boundary_utc(utc(2026, 8, 30, 6)) == bj2000(2026, 8, 28)
    # 周一白天 → 上周五 20:00（周末及周六凌晨夜盘尾巴的播报保留）
    assert context_boundary_utc(utc(2026, 8, 31, 2)) == bj2000(2026, 8, 28)
    # 周一夜里（北京 21:00）→ 周一 20:00，新交易周开始
    assert context_boundary_utc(utc(2026, 8, 31, 13)) == bj2000(2026, 8, 31)
    # naive UTC 输入按 UTC 处理
    assert context_boundary_utc(datetime(2026, 8, 26, 2)) == bj2000(2026, 8, 25)


async def test_context_history_resets_daily():
    """20:00 边界之前的播报不再进入上下文（每个战斗日全新开局）"""
    from app.services.ai_anchor.svc import build_context_history

    task = _make_task()
    old_row = _prior_broadcast(task, "上一战斗日的旧观点")
    old_row.created_at = datetime(2026, 8, 26, 6, 0, 0, tzinfo=timezone.utc)
    fresh_row = _prior_broadcast(task, "本战斗日的新观点")
    fresh_row.created_at = datetime(2026, 8, 26, 12, 30, 0, tzinfo=timezone.utc)
    session = FakeSession(broadcasts=[old_row, fresh_row])
    frozen = datetime(2026, 8, 26, 13, 0, 0, tzinfo=timezone.utc)  # 北京周三 21:00
    with patch("app.services.ai_anchor.svc.utc_now", return_value=frozen):
        history = await build_context_history(session, task)
    assert [item["commentary"] for item in history] == ["本战斗日的新观点"]


def test_parse_position_advice_field():
    """position_advice 解析：合法动作保留、非法动作丢弃"""
    from app.services.ai_anchor.prompts import parse_anchor_output

    ok = parse_anchor_output(
        '{"direction":"long","action":"trade","commentary":"x","key_levels":[],'
        '"position_advice":{"action":"add","note":"回调加仓"}}'
    )
    assert ok["position_advice"] == {"action": "add", "note": "回调加仓"}

    bad = parse_anchor_output(
        '{"direction":"long","action":"wait","commentary":"x","key_levels":[],'
        '"position_advice":{"action":"xxx","note":"非法动作"}}'
    )
    assert bad["position_advice"] is None


async def test_position_broadcast_injects_context_and_advice():
    """持仓播报：注入持仓段+浮动盈亏，模型建议落库并透传 WS payload"""
    task = _make_task()
    task.manual_position = {
        "symbol": "rb2610",
        "direction": "long",
        "price": 3200.0,
        "quantity": 5,
    }
    session = FakeSession()
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return (
            '{"direction":"long","entry":3215.0,"take_profit":3260.0,"stop_loss":3190.0,'
            '"action":"trade","commentary":"持仓浮盈，趋势完好，建议持有。",'
            '"key_levels":[],'
            '"position_advice":{"action":"hold","note":"趋势完好，持有"}}'
        )

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)

    assert result["broadcast"] is True
    system_prompt = captured["messages"][0]["content"]
    user_prompt = captured["messages"][1]["content"]
    assert "position_advice" in system_prompt and "已持仓" in system_prompt
    assert "用户当前持仓" in user_prompt
    assert "开仓价 3200" in user_prompt
    # 最新收盘价 3200+159*2.5=3597.5 → 多头浮盈 +397.5 点
    assert "+397.5" in user_prompt
    payload = mock_push.call_args[0][1]
    assert payload["broadcast"]["position_advice"] == {
        "action": "hold",
        "note": "趋势完好，持有",
    }
    row = session.broadcasts[-1]
    assert row.model_output["position_advice"]["action"] == "hold"


async def test_no_position_broadcast_unchanged():
    """未填持仓：prompt 无持仓段，payload position_advice 为空（行为不变）"""
    task = _make_task()
    session = FakeSession()
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)

    assert result["broadcast"] is True
    assert "用户当前持仓" not in captured["messages"][1]["content"]
    assert "position_advice" not in captured["messages"][0]["content"]
    payload = mock_push.call_args[0][1]
    assert payload["broadcast"]["position_advice"] is None


async def test_strategy_injected_into_prompt():
    """操盘策略注入 system prompt：策略文案随任务字段变化"""
    task = _make_task()
    task.strategy = "aggressive"
    session = FakeSession()
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor"):
        await evaluate_anchor(session, task, redis_conn=None)

    system_prompt = captured["messages"][0]["content"]
    assert "操盘策略【激进】" in system_prompt


def test_strategy_prompt_covers_all_modes():
    """三档策略文案齐备，未知值回退稳健"""
    from app.services.ai_anchor.prompts import _STRATEGY_CONSTRAINTS, build_system_prompt

    assert set(_STRATEGY_CONSTRAINTS) == {"aggressive", "balanced", "conservative"}
    assert "操盘策略【稳健】" in build_system_prompt("any", strategy="balanced")
    assert "操盘策略【保守】" in build_system_prompt("any", strategy="conservative")
    # 未知策略回退稳健
    assert "操盘策略【稳健】" in build_system_prompt("any", strategy="whatever")


def test_horizon_prompt_covers_all_modes():
    """三档交易风格文案齐备，未知值回退短线"""
    from app.services.ai_anchor.prompts import _HORIZON_CONSTRAINTS, build_system_prompt

    assert set(_HORIZON_CONSTRAINTS) == {"short", "mid", "long"}
    assert "交易风格【短线】" in build_system_prompt("any", horizon="short")
    assert "交易风格【中线】" in build_system_prompt("any", horizon="mid")
    assert "交易风格【长线】" in build_system_prompt("any", horizon="long")
    # 未知风格回退短线
    assert "交易风格【短线】" in build_system_prompt("any", horizon="whatever")


async def test_horizon_injected_into_prompt():
    """交易风格注入 system prompt：风格文案随任务字段变化"""
    task = _make_task(horizon="mid")
    session = FakeSession()
    captured: dict[str, Any] = {}

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history",
        new_callable=AsyncMock,
        return_value=_make_bars(160),
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor"):
        await evaluate_anchor(session, task, redis_conn=None)

    system_prompt = captured["messages"][0]["content"]
    assert "交易风格【中线】" in system_prompt


async def test_multi_timeframe_prompt_and_broadcast():
    """多时段任务：每时段独立取K线/算指标，单次调用综合分析，播报周期=主时段"""
    task = _make_task(timeframes=["5m", "15m", "60m"])
    session = FakeSession()
    captured: dict[str, Any] = {}
    requested_tfs: list[str] = []

    async def _kline_by_tf(redis_conn, symbol, timeframe, **kwargs):
        requested_tfs.append(timeframe)
        return _make_bars(160)

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history", side_effect=_kline_by_tf
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)

    assert result["broadcast"] is True and result["model_error"] is False
    # 三个时段各自取数
    assert requested_tfs == ["5m", "15m", "60m"]
    system_prompt = captured["messages"][0]["content"]
    user_prompt = captured["messages"][1]["content"]
    # system prompt：多周期共振指令
    assert "3 个分时K线时段" in system_prompt
    assert "多周期共振" in system_prompt
    # user prompt：三段各带时段头与K线/指标块，主时段标注
    assert "分时K线段：5m/15m/60m" in user_prompt
    assert "【时段1：5m（主时段）" in user_prompt
    assert "【时段2：15m，" in user_prompt
    assert "【时段3：60m，" in user_prompt
    # 每段都有自己的指标摘要 JSON
    assert user_prompt.count('"name": "MACD"') == 3
    # 播报落库与 WS 推送的周期 = 主时段
    row = session.broadcasts[-1]
    assert row.timeframe == "5m"
    assert mock_push.call_args.args[1]["broadcast"]["timeframe"] == "5m"


async def test_multi_timeframe_partial_shortage_skips_segment():
    """部分时段数据不足：跳过该时段，其余时段正常综合播报"""
    task = _make_task(timeframes=["15m", "60m"])
    session = FakeSession()
    captured: dict[str, Any] = {}

    async def _kline_by_tf(redis_conn, symbol, timeframe, **kwargs):
        return _make_bars(5) if timeframe == "60m" else _make_bars(160)

    async def _fake_chat(session_arg, user_id, model_row_id, messages, temperature, max_tokens):
        captured["messages"] = messages
        return _MODEL_JSON

    with patch(
        "app.services.ai_anchor.engine.get_kline_history", side_effect=_kline_by_tf
    ), patch(
        "app.services.ai_anchor.engine.chat_once", side_effect=_fake_chat
    ), patch("app.api.ws.broadcast_anchor"):
        result = await evaluate_anchor(session, task, redis_conn=None)

    assert result["broadcast"] is True and result["model_error"] is False
    user_prompt = captured["messages"][1]["content"]
    # 只剩可用时段进 prompt；缺数据的 60m 不出现
    assert "【时段1：15m（主时段）" in user_prompt
    assert "60m" not in user_prompt.split("【你的最近播报记录")[0].replace("分时K线段：", "")
    assert "【时段2" not in user_prompt
    row = session.broadcasts[-1]
    assert row.timeframe == "15m"


async def test_multi_timeframe_all_shortage_degrades():
    """全部时段数据不足：降级观望播报（model_error），周期=配置主时段"""
    task = _make_task(timeframes=["5m", "15m"])
    session = FakeSession()

    async def _kline_by_tf(redis_conn, symbol, timeframe, **kwargs):
        return _make_bars(3)

    with patch(
        "app.services.ai_anchor.engine.get_kline_history", side_effect=_kline_by_tf
    ), patch("app.api.ws.broadcast_anchor") as mock_push:
        result = await evaluate_anchor(session, task, redis_conn=None)

    assert result["broadcast"] is True and result["model_error"] is True
    row = session.broadcasts[-1]
    assert row.action == "wait"
    assert "数据不足" in row.commentary
    assert row.timeframe == "5m"
    mock_push.assert_called_once()


async def test_timeframes_none_falls_back_to_single():
    """timeframes 为 NULL（存量任务）：回退单周期列，行为与单时段一致"""
    from app.services.ai_anchor.svc import task_timeframes

    task = _make_task()
    task.timeframes = None
    assert task_timeframes(task) == ["15m"]
