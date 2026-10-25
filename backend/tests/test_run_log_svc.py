"""L1 运行日志测试 —— 写入 best-effort / 查询过滤 / detail 分层"""

from __future__ import annotations

import pytest

from app.models.ai_trading import AITradingRunLog, AITradingTask
from app.services.ai_trading.task.run_log_svc import list_run_logs, write_run_log


class _FakeTask(AITradingTask):
    """不落库的轻量替身：只提供 write_run_log 需要的字段"""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.user_id = kw.get("user_id")
        self.timeframe = kw.get("timeframe", "15m")
        self.symbol = kw.get("symbol", "rb2610")
        self.strategy_type = kw.get("strategy_type", "factor")
        self.side_mode = kw.get("side_mode", "both")
        self.last_bar_time = kw.get("last_bar_time", "2026-08-15 09:15")

    def __init_subclass__(cls, **kw):
        pass


class _StubSession:
    """最小 session 桩：add + flush；flush 抛错时验证 best-effort"""

    def __init__(self, fail_flush=False):
        self.added: list = []
        self.fail_flush = fail_flush

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.fail_flush:
            raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_write_run_log_basic():
    sess = _StubSession()
    task = _FakeTask()
    await write_run_log(
        session=sess, task=task,
        event="signal", action="open_long",
        reason="因子偏多(0.45)开多",
        detail={"factor": {"signal": {"position": 0.45}}},
    )
    assert len(sess.added) == 1
    row = sess.added[0]
    assert isinstance(row, AITradingRunLog)
    assert row.event == "signal" and row.level == "info"
    assert row.strategy_type == "factor"
    assert row.reason == "因子偏多(0.45)开多"
    assert row.detail["factor"]["signal"]["position"] == 0.45


@pytest.mark.asyncio
async def test_write_run_log_best_effort_no_raise():
    """DB 故障时写入静默失败（不阻塞交易主路径）"""
    sess = _StubSession(fail_flush=True)
    task = _FakeTask()
    # 不应抛出
    await write_run_log(session=sess, task=task, event="error", reason="x" * 3000)
    assert len(sess.added) == 1
    assert len(sess.added[0].reason) <= 2000  # reason 截断


@pytest.mark.asyncio
async def test_list_run_logs_filters():
    """查询过滤：level/event/task_id 组合"""
    from sqlalchemy import select  # noqa: F401

    class _Exec:
        def __init__(self, rows, total):
            self._rows, self._total = rows, total

        def scalar(self):
            return self._total

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _Sess:
        def __init__(self, rows, total):
            self._e = _Exec(rows, total)
            self.calls = []

        async def execute(self, stmt):
            self.calls.append(stmt)
            return self._e

    row = AITradingRunLog(
        task_id="t1", user_id="u1", level="warn", event="hard_rule",
        action="close", reason="止损触发",
    )
    row.created_at = None
    rows, total = await list_run_logs(
        _Sess([row], 1), task_id="t1", level="warn", event="hard_rule"
    )
    assert total == 1
    assert rows[0]["event"] == "hard_rule"
    assert rows[0]["reason"] == "止损触发"
