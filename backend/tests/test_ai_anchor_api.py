"""AI 看盘主播 API 契约测试 —— 依赖覆盖 + 内存 FakeSession

覆盖：鉴权、start 参数校验/模型归属、pause/resume/stop 生命周期、status/broadcasts。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.models.ai_anchor import AIAnchorBroadcast, AIAnchorTask
from app.models.user import User


def _make_user() -> User:
    return User(
        id=uuid4(),
        username="anchor-tester",
        hashed_password="x",
        role="user",
        status="active",
    )


class _Scalars(list):
    """模拟 Result.scalars().all()"""

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
    """内存版 AsyncSession：支持 ai_anchor 用到的等值 select / 排序 / limit"""

    def __init__(self) -> None:
        self.tasks: list[AIAnchorTask] = []
        self.broadcasts: list[AIAnchorBroadcast] = []
        self._pending: list[Any] = []

    @staticmethod
    def _where(stmt: Any) -> dict[str, Any]:
        wc = getattr(stmt, "whereclause", None)
        if wc is None:
            return {}
        out: dict[str, Any] = {}
        for clause in getattr(wc, "clauses", [wc]):
            value = getattr(clause.right, "value", clause.right)
            # 只取简单值比较（str/bool/int/float/UUID/None），其余条件跳过
            if isinstance(value, (str, bool, int, float, type(None))) or isinstance(
                value, uuid.UUID
            ):
                out[clause.left.key] = value
        return out

    @staticmethod
    def _order_desc_keys(stmt: Any) -> list[str]:
        out: list[str] = []
        for ob in getattr(stmt, "_order_by_clauses", ()):
            if hasattr(ob, "element"):  # desc
                out.append(ob.element.key)
        return out

    async def execute(self, stmt: Any) -> _FakeResult:
        await self.flush()
        entity = stmt.column_descriptions[0]["entity"]
        rows: list[Any]
        if entity is AIAnchorTask:
            rows = list(self.tasks)
        elif entity is AIAnchorBroadcast:
            rows = list(self.broadcasts)
        else:
            raise AssertionError(f"FakeSession 不支持 {entity}")
        flt = self._where(stmt)
        if flt:
            rows = [r for r in rows if all(getattr(r, k) == v for k, v in flt.items())]
        for key in reversed(self._order_desc_keys(stmt)):
            rows.sort(
                key=lambda r: getattr(r, key) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
        limit = getattr(stmt, "_limit", None)
        return _FakeResult(rows[:limit] if limit else rows)

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    async def flush(self) -> None:
        for obj in self._pending:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if isinstance(obj, AIAnchorTask):
                self.tasks.append(obj)
            else:
                self.broadcasts.append(obj)
        self._pending.clear()

    async def commit(self) -> None:
        await self.flush()

    async def rollback(self) -> None:
        self._pending.clear()


def _get_test_app(user: User | None, session: FakeSession | None) -> Any:
    """构建带依赖覆盖的测试 app（覆盖 get_auth_context + get_session）"""
    from app.core.deps import AuthContext, get_auth_context, get_session
    from app.main import app

    async def _mock_session():
        yield session

    async def _mock_ctx():
        return AuthContext(user=user, trading_mode="live")

    app.dependency_overrides[get_session] = _mock_session
    app.dependency_overrides[get_auth_context] = _mock_ctx
    return app


def _clear_overrides() -> None:
    from app.main import app

    app.dependency_overrides.clear()


def _start_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model_row_id": str(uuid4()),
        "symbol": "RB2610",
        "timeframe": "15m",
        "direction_mode": "any",
        "bar_count": 60,
        "interval_minutes": 15,
        "indicators": [
            {"name": "MACD", "params": {}},
            {"name": "MA", "params": {"period": 20}},
        ],
    }
    body.update(overrides)
    return body


async def test_anchor_status_requires_auth():
    """未带 token → 401"""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/ai-anchor/status")
        assert resp.status_code == 401


async def test_anchor_status_empty():
    """无任务 → task 为 null + 空播报"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/ai-anchor/status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["task"] is None
            assert data["recent_broadcasts"] == []
    finally:
        _clear_overrides()


async def test_anchor_start_happy_path():
    """正常启动：200 + status=running + 指标参数归一化 + 品种小写 + 方向偏好落库"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/ai-anchor/start", json=_start_body(direction_mode="short")
                )
                assert resp.status_code == 200
                task = resp.json()["task"]
                assert task["status"] == "running"
                assert task["symbol"] == "rb2610"
                assert task["timeframe"] == "15m"
                assert task["direction_mode"] == "short"
                indicators = {item["name"]: item["params"] for item in task["indicators"]}
                # MACD 缺省参数补默认 12/26/9
                assert indicators["MACD"] == {"fast": 12, "slow": 26, "signal": 9}
                assert indicators["MA"] == {"period": 20}
    finally:
        _clear_overrides()


async def test_anchor_start_direction_default_and_invalid():
    """direction_mode 缺省=any；非法值 422（Literal 校验）"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                body = _start_body()
                del body["direction_mode"]
                resp = await client.post("/api/ai-anchor/start", json=body)
                assert resp.status_code == 200
                assert resp.json()["task"]["direction_mode"] == "any"

                resp = await client.post(
                    "/api/ai-anchor/start", json=_start_body(direction_mode="both")
                )
                assert resp.status_code == 422
    finally:
        _clear_overrides()


async def test_anchor_start_model_not_found():
    """模型不存在/不属于当前用户 → 400"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/ai-anchor/start", json=_start_body())
                assert resp.status_code == 400
    finally:
        _clear_overrides()


async def test_anchor_start_validation_errors():
    """根数越界 → 422；不支持的技术线 → 400"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/ai-anchor/start", json=_start_body(bar_count=10)
                )
                assert resp.status_code == 422
                resp = await client.post(
                    "/api/ai-anchor/start",
                    json=_start_body(interval_minutes=45),
                )
                assert resp.status_code == 422
                resp = await client.post(
                    "/api/ai-anchor/start",
                    json=_start_body(
                        indicators=[{"name": "CCI", "params": {}}],
                    ),
                )
                assert resp.status_code == 400
    finally:
        _clear_overrides()


async def test_anchor_lifecycle_pause_resume_stop():
    """start → pause → resume → stop 状态流转；next_run_at 即时到期"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/ai-anchor/start", json=_start_body())
                assert resp.status_code == 200
                task_id = resp.json()["task"]["id"]

                resp = await client.post("/api/ai-anchor/pause")
                assert resp.status_code == 200
                assert resp.json()["task"]["status"] == "paused"

                resp = await client.post("/api/ai-anchor/resume")
                assert resp.status_code == 200
                assert resp.json()["task"]["status"] == "running"

                resp = await client.post("/api/ai-anchor/stop")
                assert resp.status_code == 200
                assert resp.json()["task"]["status"] == "stopped"

                # status 回读任务 + 空（FakeSession 无播报）
                resp = await client.get("/api/ai-anchor/status")
                assert resp.status_code == 200
                assert resp.json()["task"]["id"] == task_id
                assert resp.json()["task"]["status"] == "stopped"
    finally:
        _clear_overrides()


async def test_anchor_pause_without_task_404():
    """无任务时 pause/stop → 404"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/ai-anchor/pause")
            assert resp.status_code == 404
            resp = await client.post("/api/ai-anchor/stop")
            assert resp.status_code == 404
            resp = await client.get("/api/ai-anchor/broadcasts")
            assert resp.status_code == 404
    finally:
        _clear_overrides()


async def test_anchor_start_multi_timeframes_and_horizon():
    """多时段启动：timeframes 数组落库 + 主时段冗余 + 交易风格入库与回读"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/ai-anchor/start",
                    json=_start_body(
                        timeframes=["5m", "15m", "60m"],
                        horizon="mid",
                    ),
                )
                assert resp.status_code == 200
                task = resp.json()["task"]
                assert task["status"] == "running"
                assert task["timeframes"] == ["5m", "15m", "60m"]
                # timeframe 冗余主时段，旧消费方（图解弹窗等）不受影响
                assert task["timeframe"] == "5m"
                assert task["horizon"] == "mid"
                # 交易风格缺省 = 短线
                resp = await client.post(
                    "/api/ai-anchor/start", json=_start_body(timeframes=["15m"])
                )
                assert resp.status_code == 200
                assert resp.json()["task"]["horizon"] == "short"
    finally:
        _clear_overrides()


async def test_anchor_start_timeframes_validation():
    """timeframes 校验：>3 个 / 非法周期 / 与 timeframe 都缺 → 422"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 4 个时段超上限
                resp = await client.post(
                    "/api/ai-anchor/start",
                    json=_start_body(timeframes=["1m", "5m", "15m", "60m"]),
                )
                assert resp.status_code == 422
                # 非法周期值
                resp = await client.post(
                    "/api/ai-anchor/start",
                    json=_start_body(timeframes=["5m", "1h"]),
                )
                assert resp.status_code == 422
                # 两个周期字段都缺
                body = _start_body(timeframes=None)
                del body["timeframe"]
                resp = await client.post("/api/ai-anchor/start", json=body)
                assert resp.status_code == 422
                # 交易风格非法值
                resp = await client.post(
                    "/api/ai-anchor/start",
                    json=_start_body(timeframes=["15m"], horizon="swing"),
                )
                assert resp.status_code == 422
    finally:
        _clear_overrides()


async def test_anchor_patch_horizon_hot_switch():
    """PATCH /config 热切交易风格：下一轮生效，不动状态与下次播报时间"""
    user = _make_user()
    session = FakeSession()
    app = _get_test_app(user, session)
    try:
        with patch(
            "app.api.ai_anchor.load_user_model",
            new_callable=AsyncMock,
            return_value=(object(), object()),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/ai-anchor/start", json=_start_body())
                assert resp.status_code == 200
                task_id = resp.json()["task"]["id"]

                resp = await client.patch(
                    "/api/ai-anchor/config", json={"horizon": "long"}
                )
                assert resp.status_code == 200
                task = resp.json()["task"]
                assert task["id"] == task_id
                assert task["horizon"] == "long"
                assert task["status"] == "running"

                # 非法风格 422
                resp = await client.patch(
                    "/api/ai-anchor/config", json={"horizon": "xxx"}
                )
                assert resp.status_code == 422
    finally:
        _clear_overrides()
