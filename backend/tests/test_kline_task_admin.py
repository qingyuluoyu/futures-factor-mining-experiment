"""K 线修正任务组后台管理测试 —— 助手/回填扫描/Admin API"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.models.user import User
from app.services.kline_task_status import (
    KLINE_TASKS,
    is_paused,
    log,
    read_logs,
    read_paused,
    read_status,
    report,
    set_paused,
)
from app.services.tickdata_gap_backfill import backfill_gaps_once


class _Pipe:
    def __init__(self, store: "_FakeRedis") -> None:
        self._store = store
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def lpush(self, key: str, value: str) -> "_Pipe":
        self._ops.append(("lpush", (key, value)))
        return self

    def ltrim(self, key: str, start: int, end: int) -> "_Pipe":
        self._ops.append(("ltrim", (key, start, end)))
        return self

    async def execute(self) -> None:
        for op, args in self._ops:
            if op == "lpush":
                self._store.lists.setdefault(args[0], []).insert(0, args[1])
            elif op == "ltrim":
                lst = self._store.lists.get(args[0], [])
                start, end = args[1], args[2]
                if end >= 0:
                    self._store.lists[args[0]] = lst[start : end + 1]
                else:
                    self._store.lists[args[0]] = lst[start:] if end == -1 else lst[start:end]
        self._ops.clear()


class _FakeRedis:
    """覆盖 kline_task_status 所需命令的假 Redis"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        await _Pipe(self).ltrim(key, start, end).execute()

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        lst = self.lists.get(key, [])
        return lst[start : end + 1] if end >= 0 else lst[start:]

    def pipeline(self) -> _Pipe:
        return _Pipe(self)


# ============ 助手 ============


async def test_report_merge_and_read():
    r = _FakeRedis()
    await report(r, "kline_reconcile", running=True)
    await report(r, "kline_reconcile", running=False, last_corrected=3)
    st = await read_status(r, "kline_reconcile")
    assert st["running"] is False
    assert st["last_corrected"] == 3  # 合并保留既有字段


async def test_log_trim_and_read():
    r = _FakeRedis()
    for i in range(250):
        await log(r, "vvtr_tickdata_sync", "info", f"m{i}")
    logs = await read_logs(r, "vvtr_tickdata_sync", limit=100)
    assert len(logs) == 100
    assert logs[0]["msg"] == "m249"  # 新在前
    all_len = len(r.lists["kline:task:vvtr_tickdata_sync:log"])
    assert all_len == 200  # LTRIM 保留 200


async def test_kline_sync_uses_legacy_keys():
    """kline_sync 沿用历史键 kline:sync:status|log(旧页面兼容)"""
    r = _FakeRedis()
    await report(r, "kline_sync", running=True)
    assert "kline:sync:status" in r.store
    assert "kline:task:kline_sync:status" not in r.store
    await log(r, "kline_sync", "info", "hi")
    assert "kline:sync:log" in r.lists
    st = await read_status(r, "kline_sync")
    assert st["running"] is True


async def test_pause_flag():
    r = _FakeRedis()
    assert await is_paused(r, "kline_reconcile") is False
    await set_paused(r, "kline_reconcile", True)
    assert await is_paused(r, "kline_reconcile") is True
    assert await read_paused(r, "kline_reconcile") is True
    await set_paused(r, "kline_reconcile", False)
    assert await is_paused(r, "kline_reconcile") is False


# ============ 缺口回填 ============


async def test_backfill_skips_masters_and_reports():
    """扫描 → 排除主力 → 逐合约 sync;stats 与状态/日志落 Redis"""
    r = _FakeRedis()

    async def _fake_find():
        return [("j2701", 10), ("rb2610", 8), ("v2701", 5)]  # rb2610 是主力

    async def _fake_masters(redis_conn):
        return {"rb2610"}

    calls: list[float] = []

    async def _fake_sync(redis_conn, days, symbols=None, session_factory=None):
        calls.append(days)
        return {"60m": 100, "15m": 200}

    with (
        patch(
            "app.services.tickdata_gap_backfill.find_gap_contracts",
            new=_fake_find,
        ),
        patch(
            "app.services.tickdata_gap_backfill._master_set",
            new=_fake_masters,
        ),
        patch(
            "app.services.vvtr_tickdata_sync.sync_masters_to_tickdata",
            new=_fake_sync,
        ),
    ):
        stats = await backfill_gaps_once(r, None, trigger="manual")

    assert stats["scanned"] == 3
    assert stats["skipped_masters"] == 1
    assert stats["candidates"] == 2  # j2701(lag10) + v2701(lag5)
    assert stats["backfilled"] == 2
    assert stats["written_rows"] == 600
    assert calls == [15, 10]  # lag+5 窗口,按 lag 降序
    st = await read_status(r, "tickdata_gap_backfill")
    assert st["running"] is False
    assert st["last_stats"]["candidates"] == 2
    logs = await read_logs(r, "tickdata_gap_backfill")
    assert any("回填 j2701" in x["msg"] for x in logs)


async def test_backfill_no_candidates_no_requests():
    r = _FakeRedis()

    async def _fake_find():
        return []

    with (
        patch(
            "app.services.tickdata_gap_backfill.find_gap_contracts", new=_fake_find
        ),
        patch(
            "app.services.vvtr_tickdata_sync.sync_masters_to_tickdata",
            new=AsyncMock(side_effect=AssertionError("不应发起 VVTR 请求")),
        ),
    ):
        stats = await backfill_gaps_once(r, None)
    assert stats["candidates"] == 0


async def test_find_gap_contracts_query():
    """扫描 SQL 走 tickdata 连接池;未配置返回空"""
    rows = [{"instrument": "j2701", "lag": 10}]

    class _Conn:
        async def fetch(self, sql, timeout=None):
            assert "period = 60" in sql
            assert "'9999', '8888'" in sql
            return rows

    class _Acq:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    class _Pool:
        def acquire(self):
            return _Acq()

    from app.services.tickdata_gap_backfill import find_gap_contracts

    async def _fake_pool():
        return _Pool()

    with patch("app.data.tickdata_kline._get_pool", new=_fake_pool):
        # find_gap_contracts 从 tickdata_kline 模块取 _get_pool,需 patch 其源
        out = await find_gap_contracts()
    assert out == [("j2701", 10)]


async def test_gap_backfill_loop_respects_owner_switch(monkeypatch):
    """非 owner 端(VVTR_TICKDATA_SYNC_DISABLED=1):loop 立即返回并上报 disabled

    33(.env 设 DISABLED=1)与 58(owner)同跑会双倍消耗 VVTR 1 rps 预算。
    """
    from app.services.tickdata_gap_backfill import (
        TASK_NAME,
        tickdata_gap_backfill_loop,
    )

    monkeypatch.setenv("VVTR_TICKDATA_SYNC_DISABLED", "1")
    r = _FakeRedis()
    # disabled 分支无 sleep,直接 await 应立即返回(进 while 循环则测试挂起超时)
    await asyncio.wait_for(tickdata_gap_backfill_loop(r), timeout=2.0)
    st = await read_status(r, TASK_NAME)
    assert st.get("disabled") is True


# ============ Admin API ============


def _get_test_app(admin: User | None = None):
    from app.core.deps import get_current_admin, get_session
    from app.main import app

    fake_redis = _FakeRedis()

    async def _mock_session():
        yield None

    async def _mock_admin():
        return admin

    app.dependency_overrides[get_session] = _mock_session
    if admin is not None:
        app.dependency_overrides[get_current_admin] = _mock_admin
    app.state.redis = fake_redis
    return app, fake_redis


def _clear():
    from app.main import app

    app.dependency_overrides.clear()


def _admin_user() -> User:
    return User(
        id=__import__("uuid").uuid4(),
        username="admin",
        hashed_password="x",
        role="admin",
        status="active",
    )


async def test_kline_tasks_requires_admin():
    app, _ = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/admin/kline-tasks")
            assert resp.status_code in (401, 403)
    finally:
        _clear()


async def test_kline_tasks_aggregate_and_controls():
    app, r = _get_test_app(_admin_user())
    try:
        await report(r, "kline_reconcile", running=False, last_corrected=7)
        await log(r, "kline_reconcile", "info", "hello")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/admin/kline-tasks")
            assert resp.status_code == 200
            tasks = resp.json()["tasks"]
            assert [t["name"] for t in tasks] == list(KLINE_TASKS)
            by = {t["name"]: t for t in tasks}
            assert by["kline_reconcile"]["status"]["last_corrected"] == 7
            assert by["post_deploy_reconcile"]["pausable"] is False

            # pause / resume
            resp = await client.post("/api/admin/kline-tasks/kline_reconcile/pause")
            assert resp.status_code == 200 and resp.json()["paused"] is True
            assert await read_paused(r, "kline_reconcile") is True
            resp = await client.post("/api/admin/kline-tasks/kline_reconcile/resume")
            assert resp.status_code == 200 and resp.json()["paused"] is False

            # 一次性任务不可控
            resp = await client.post(
                "/api/admin/kline-tasks/post_deploy_reconcile/pause"
            )
            assert resp.status_code == 400
            resp = await client.post(
                "/api/admin/kline-tasks/post_deploy_reconcile/run"
            )
            assert resp.status_code == 400

            # 未知任务
            resp = await client.post("/api/admin/kline-tasks/nope/pause")
            assert resp.status_code == 404

            # 手动触发(底层函数 mock,验证 create_task 路径)
            with patch(
                "app.services.vvtr_tickdata_sync.sync_masters_to_tickdata",
                new=AsyncMock(return_value={"60m": 5}),
            ):
                resp = await client.post(
                    "/api/admin/kline-tasks/vvtr_tickdata_sync/run"
                )
                assert resp.status_code == 200
                assert resp.json() == {"ok": True, "started": True, "name": "vvtr_tickdata_sync"}
                import asyncio

                await asyncio.sleep(0.1)  # 让 create_task 跑完

            # 日志
            resp = await client.get("/api/admin/kline-tasks/kline_reconcile/logs")
            assert resp.status_code == 200
            logs = resp.json()["logs"]
            assert logs and logs[0]["msg"] == "hello"

            # kline_sync 聚合读旧键
            r.store["kline:sync:status"] = json.dumps({"running": True})
            resp = await client.get("/api/admin/kline-tasks")
            by = {t["name"]: t for t in resp.json()["tasks"]}
            assert by["kline_sync"]["status"]["running"] is True
    finally:
        _clear()
