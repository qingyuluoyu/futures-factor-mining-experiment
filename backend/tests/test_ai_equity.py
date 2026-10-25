from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai_trading.equity import list_equity_series, snapshot_equity


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarRows":
        return self

    def all(self) -> list[object]:
        return self._rows


class _EventRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


@pytest.mark.asyncio
async def test_equity_series_restores_realized_loss_from_filled_orders() -> None:
    task_id = uuid4()
    user_id = uuid4()
    event_ts = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    point_before = SimpleNamespace(
        task_id=task_id,
        ts=event_ts - timedelta(seconds=1),
        equity=Decimal("100000"),
        cash_delta=Decimal("0"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
    )
    point_after = SimpleNamespace(
        task_id=task_id,
        ts=event_ts + timedelta(seconds=1),
        equity=Decimal("99900"),
        cash_delta=Decimal("-20"),
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("-20"),
    )
    events = _EventRows(
        [(task_id, event_ts, event_ts, event_ts, Decimal("-80"))]
    )
    equity_rows = _ScalarRows([point_after, point_before])
    session = AsyncMock()
    session.add = MagicMock()

    async def execute(stmt: object) -> object:
        return events if "paper_orders" in str(stmt) else equity_rows

    session.execute.side_effect = execute

    result = await list_equity_series(session, user_id, [task_id], 500)

    points = result[str(task_id)]
    assert [point["cash_delta"] for point in points] == [0.0, -100.0]
    assert points[-1]["realized_pnl"] == -80.0
    assert points[-1]["unrealized_pnl"] == -20.0


@pytest.mark.asyncio
async def test_snapshot_equity_persists_total_task_pnl() -> None:
    task_id = uuid4()
    user_id = uuid4()
    event_ts = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        trading_mode="live",
        symbol="ma2701",
        strategy_type="ai",
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.return_value = _EventRows(
        [(task_id, event_ts, event_ts, event_ts, Decimal("-80"))]
    )

    with (
        patch(
            "app.services.ai_trading.equity.get_account_summary",
            new=AsyncMock(return_value={"total_equity": 99900}),
        ),
        patch(
            "app.services.ai_trading.task.position_pnl.get_task_position_pnl",
            new=AsyncMock(return_value={"unrealized_pnl": -20}),
        ),
    ):
        result = await snapshot_equity(session, task)

    saved = session.add.call_args.args[0]
    assert float(saved.realized_pnl) == -80.0
    assert float(saved.unrealized_pnl) == -20.0
    assert float(saved.cash_delta) == -100.0
    assert result["cash_delta"] == -100.0
