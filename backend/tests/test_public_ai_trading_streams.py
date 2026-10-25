"""对外公开流 / showcase 当日已实现 / 权益区间 单测

- list_public_decisions / list_public_trades：keyset 分页壳、脱敏、孤儿成交
- _today_realized_by_tasks：日界过滤与同口径聚合
- _list_equity_public：区间模式升序 / 默认最近 N 点
- list_showcase：today_realized 落到 tasks / profit_items / totals
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.public import _validate_cursor
from app.services.ai_trading.task import public_stream_svc, showcase_svc

UTC = timezone.utc


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def scalars(self) -> "_Rows":
        return self

    def first(self):
        return self._rows[0] if self._rows else None


def _decision(row_id, created_at, **over) -> SimpleNamespace:
    base = dict(
        id=row_id,
        task_id=uuid4(),
        user_id=uuid4(),
        bar_time="2026-09-09 09:15:00",
        timeframe="15m",
        trigger_type="bar",
        context_summary={"bars": 40},
        model_output={"action": "hold", "qty": 0},
        action="hold",
        order_id=None,
        reason="观望",
        created_at=created_at,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _order(row_id, ts, *, filled_at=None, **over) -> SimpleNamespace:
    base = dict(
        id=row_id,
        task_id=uuid4(),
        symbol="rb2701",
        symbol_name="螺纹钢2701",
        direction="buy",
        offset="open",
        price=Decimal("3500.0"),
        quantity=2,
        filled_qty=2,
        status="filled",
        frozen_margin=Decimal("7000.0"),
        fee=Decimal("8.4"),
        realized_pnl=Decimal("0"),
        multiplier=10,
        margin_rate=Decimal("0.1"),
        created_at=ts,
        updated_at=ts,
        filled_at=filled_at,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_public_decisions_pages_and_sanitizes() -> None:
    t0 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    rows = [
        (_decision(uuid4(), t0), "任务A", "rb2701", "螺纹钢2701"),
        (
            _decision(uuid4(), t0 + timedelta(minutes=1)),
            "任务B",
            "ma2701",
            "甲醇2701",
        ),
        (
            _decision(uuid4(), t0 + timedelta(minutes=2)),
            "任务C",
            "i2701",
            "铁矿石2701",
        ),
    ]
    session = AsyncMock()
    session.execute.return_value = _Rows(rows)

    result = await public_stream_svc.list_public_decisions(
        session, limit=2
    )

    assert result["count"] == 2
    assert result["has_more"] is True
    assert result["items"][0]["task_name"] == "任务A"
    assert result["next_since_ts"] == rows[1][0].created_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert result["next_since_id"] == str(rows[1][0].id)
    for item in result["items"]:
        assert "user_id" not in item
        assert "context_summary" not in item
        assert item["model_output"] == {"action": "hold", "qty": 0}


@pytest.mark.asyncio
async def test_public_decisions_last_page_has_no_more() -> None:
    t0 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    rows = [(_decision(uuid4(), t0), "任务A", "rb2701", "螺纹钢2701")]
    session = AsyncMock()
    session.execute.return_value = _Rows(rows)

    result = await public_stream_svc.list_public_decisions(session, limit=200)

    assert result["count"] == 1
    assert result["has_more"] is False
    assert result["next_since_id"] == str(rows[0][0].id)


@pytest.mark.asyncio
async def test_public_trades_include_orphans_and_fee() -> None:
    ts = datetime(2026, 9, 9, 1, 30, tzinfo=UTC)
    live = _order(uuid4(), ts, filled_at=ts, realized_pnl=Decimal("-120"))
    orphan = _order(
        uuid4(), ts + timedelta(minutes=1), filled_at=None, offset="close"
    )
    session = AsyncMock()
    session.execute.return_value = _Rows([(live, "任务A"), (orphan, None)])

    result = await public_stream_svc.list_public_trades(session, limit=200)

    assert result["count"] == 2
    first, second = result["items"]
    assert first["task_name"] == "任务A"
    assert first["fee"] == 8.4
    assert first["realized_pnl"] == -120.0
    assert first["order_id"] == first["id"]
    assert first["ts"].endswith("Z")
    # 孤儿成交：任务已删除，ts 回退 created_at（filled_at 为空）
    assert second["task_name"] is None
    assert second["ts"] == orphan.created_at.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    "since_ts,since_id,ok",
    [
        (None, None, True),
        (datetime(2026, 9, 9, tzinfo=UTC), uuid4(), True),
        (datetime(2026, 9, 9, tzinfo=UTC), None, False),
        (None, uuid4(), False),
    ],
)
def test_validate_cursor_requires_pair(since_ts, since_id, ok) -> None:
    if ok:
        _validate_cursor(since_ts, since_id)
    else:
        with pytest.raises(HTTPException) as exc:
            _validate_cursor(since_ts, since_id)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_today_realized_filters_day_and_maps() -> None:
    task_id = uuid4()
    captured: list[str] = []
    session = AsyncMock()

    async def execute(stmt: object) -> _Rows:
        captured.append(str(stmt))
        return _Rows([(task_id, Decimal("88.5"))])

    session.execute.side_effect = execute

    result = await showcase_svc._today_realized_by_tasks(session, [task_id])

    assert result == {task_id: 88.5}
    sql = captured[0]
    # 同口径：decision 关联 + filled close + 日界在 filled_at/created_at 上
    assert "ai_trading_decisions" in sql and "paper_orders" in sql
    assert "coalesce" in sql and "filled_at" in sql


def _equity_point(task_id, ts) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        ts=ts,
        equity=Decimal("100000"),
        cash_delta=Decimal("10"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("10"),
    )


@pytest.mark.asyncio
async def test_equity_range_mode_returns_asc_without_reverse() -> None:
    task_id = uuid4()
    t0 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    # 模拟 DB 按 ASC 返回
    rows = [
        _equity_point(task_id, t0),
        _equity_point(task_id, t0 + timedelta(minutes=1)),
    ]
    session = AsyncMock()
    session.execute.return_value = _Rows(rows)

    series = await showcase_svc._list_equity_public(
        session, [task_id], 400,
        ts_from=t0 - timedelta(hours=1), ts_to=t0 + timedelta(hours=1),
    )

    points = series[str(task_id)]
    assert [p["ts"] for p in points] == [
        t0.isoformat().replace("+00:00", "Z"),
        (t0 + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    ]


@pytest.mark.asyncio
async def test_equity_default_mode_reverses_desc_rows() -> None:
    task_id = uuid4()
    t0 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    # 模拟 DB 按 DESC 返回，函数应反转为 ASC
    rows = [
        _equity_point(task_id, t0 + timedelta(minutes=1)),
        _equity_point(task_id, t0),
    ]
    session = AsyncMock()
    session.execute.return_value = _Rows(rows)

    series = await showcase_svc._list_equity_public(session, [task_id], 400)

    points = series[str(task_id)]
    assert points[0]["ts"] == t0.isoformat().replace("+00:00", "Z")


def _showcase_task(task_id, user_id) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        user_id=user_id,
        name="任务A",
        model_row_id=None,
        strategy_type="ai",
        trading_mode="live",
        symbol="rb2701",
        symbol_name="螺纹钢2701",
        timeframe="15m",
        side_mode="both",
        position_mode="single",
        fixed_qty=1,
        close_rules={},
        stop_rules={},
        status="running",
        pause_reason=None,
        equity_baseline=100000.0,
        close_on_stop=False,
        last_run_at=None,
        next_bar_ts=None,
        last_bar_time=None,
        started_at=None,
        paused_at=None,
        stopped_at=None,
        runtime_seconds=0,
        run_started_at=None,
        note=None,
        created_at=None,
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_list_showcase_exposes_today_realized() -> None:
    task_id = uuid4()
    user_id = uuid4()
    task = _showcase_task(task_id, user_id)
    t0 = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    session = AsyncMock()

    async def execute(stmt: object) -> _Rows:
        sql = str(stmt)
        if "ai_trading_equity_points" in sql:
            return _Rows([_equity_point(task_id, t0)])
        if "sum" in sql:
            # 生命周期累计 realized 与当日 today_realized 两条聚合
            if "coalesce" in sql and "filled_at" in sql:
                return _Rows([(task_id, Decimal("88.5"))])
            return _Rows([(task_id, Decimal("150"))])
        if "ai_trading_tasks" in sql:
            # 第二条补 paused 的查询带 NOT IN（排除已取到的 running）
            if "NOT IN" in sql.upper():
                return _Rows([])
            return _Rows([task])
        return _Rows([])

    session.execute.side_effect = execute

    with (
        patch.object(
            showcase_svc, "get_task_position_pnl",
            new=AsyncMock(
                return_value={
                    "direction": "long",
                    "quantity": 1,
                    "avg_price": 3500.0,
                    "last_price": 3520.0,
                    "unrealized_pnl": 200.0,
                    "cash_delta": 200.0,
                }
            ),
        ),
    ):
        result = await showcase_svc.list_showcase(session, None, 12, None)

    assert result["tasks"][0]["today_realized"] == 88.5
    assert result["tasks"][0]["user_id"] == ""
    assert result["profit_items"][0]["today_realized"] == 88.5
    assert result["total_today_realized"] == 88.5
    assert result["total_realized"] == 150.0
    assert result["total_pnl"] == 350.0


@pytest.mark.asyncio
async def test_list_showcase_empty_tasks_includes_today_total() -> None:
    session = AsyncMock()
    session.execute.return_value = _Rows([])

    result = await showcase_svc.list_showcase(session, None, 12, None)

    assert result["tasks"] == []
    assert result["total_today_realized"] == 0.0


def test_ensure_utc_treats_naive_as_utc() -> None:
    naive = datetime(2026, 9, 9, 1, 0)
    assert public_stream_svc._ensure_utc(naive).tzinfo is not None
    aware = datetime(2026, 9, 9, 1, 0, tzinfo=UTC)
    assert public_stream_svc._ensure_utc(aware) is aware
