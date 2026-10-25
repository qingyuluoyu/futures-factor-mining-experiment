"""POST /api/factor-lab/history —— 桌面端本地搜索结果落历史库

HTTP 层用依赖覆盖 + mock upsert 验证契约(鉴权/校验/source 写死/事务),
FIFO/去重语义用内存 FakeSession 直接跑真实 upsert_champions_fifo/list_history。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.models.factor_lab import FactorHistory
from app.models.user import User
from app.services.factor_lab.persist.history_svc import (
    HISTORY_CAP,
    list_history,
    upsert_champions_fifo,
)


def _make_user() -> User:
    return User(
        id=uuid4(),
        username="tester",
        hashed_password="x",
        role="user",
        status="active",
    )


def _champ(tokens: list[int], composite: float = 1.0, text: str = "f") -> dict:
    return {"tokens": tokens, "text": text, "composite": composite, "metrics": {}}


# ============ HTTP 层契约 ============


def _get_test_app(user: User | None = None) -> Any:
    """构建带依赖覆盖的测试 app(风格对齐 test_ai_skills_api)"""
    from app.core.deps import get_current_user, get_session
    from app.main import app

    async def _mock_session():
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.flush = AsyncMock()
        session.add = AsyncMock()
        session.delete = AsyncMock()
        yield session

    async def _mock_user():
        return user

    app.dependency_overrides[get_session] = _mock_session
    if user is not None:
        app.dependency_overrides[get_current_user] = _mock_user
    return app


def _clear_overrides() -> None:
    from app.main import app

    app.dependency_overrides.clear()


async def test_save_history_requires_auth():
    """未带 token → 401"""
    app = _get_test_app()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/factor-lab/history", json={"symbol": "rb2610", "champions": []}
            )
            assert resp.status_code == 401
    finally:
        _clear_overrides()


async def test_save_history_happy_path():
    """正常保存:200 + upsert 参数正确(source=local、composite 舍入 4 位)"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        with patch(
            "app.api.factor_lab.history_api.upsert_champions_fifo",
            new_callable=AsyncMock,
            return_value=3,
        ) as mock_upsert:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/factor-lab/history",
                    json={
                        "symbol": "rb2610",
                        "timeframe": "1d",
                        "champions": [
                            _champ([3, 17, 5], 1.23456, "ts_rank(close, 5)"),
                            _champ([0], 0.9876, "RET"),
                            _champ([1, 2], 0.5, "ADD"),
                        ],
                    },
                )
                assert resp.status_code == 200
                assert resp.json() == {"ok": True, "saved": 3}
        mock_upsert.assert_awaited_once()
        args = mock_upsert.await_args.args
        # (session, user_id, symbol, timeframe, champions, source) 全位置传参
        assert args[1] == user.id
        assert args[2] == "rb2610"
        assert args[3] == "1d"
        assert args[5] == "local"
        payload = args[4]
        assert len(payload) == 3
        assert payload[0]["composite"] == 1.2346  # champion_payload 舍入 4 位
        assert payload[0]["tokens"] == [3, 17, 5]
    finally:
        _clear_overrides()


async def test_save_history_ignores_client_source():
    """客户端伪造 source 字段被忽略(模型无该字段,服务端写死 local)"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        with patch(
            "app.api.factor_lab.history_api.upsert_champions_fifo",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_upsert:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/factor-lab/history",
                    json={
                        "symbol": "rb2610",
                        "champions": [_champ([0])],
                        "source": "gp",  # 应被忽略
                    },
                )
                assert resp.status_code == 200
        assert mock_upsert.await_args.args[5] == "local"
    finally:
        _clear_overrides()


async def test_save_history_validation_errors():
    """champions 空/超 20 条、symbol 空白 → 422"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/factor-lab/history",
                json={"symbol": "rb2610", "champions": []},
            )
            assert resp.status_code == 422

            resp = await client.post(
                "/api/factor-lab/history",
                json={
                    "symbol": "rb2610",
                    "champions": [_champ([i]) for i in range(21)],
                },
            )
            assert resp.status_code == 422

            resp = await client.post(
                "/api/factor-lab/history",
                json={"symbol": "   ", "champions": [_champ([0])]},
            )
            assert resp.status_code == 422

            # 缺 symbol → Pydantic 422
            resp = await client.post(
                "/api/factor-lab/history",
                json={"champions": [_champ([0])]},
            )
            assert resp.status_code == 422
    finally:
        _clear_overrides()


async def test_save_history_db_error_rolls_back():
    """落库异常 → rollback + 500"""
    user = _make_user()
    app = _get_test_app(user)
    from app.core.deps import get_session

    rollback_mock = AsyncMock()
    commit_mock = AsyncMock()

    async def _mock_session():
        session = AsyncMock()
        session.commit = commit_mock
        session.rollback = rollback_mock
        yield session

    app.dependency_overrides[get_session] = _mock_session
    try:
        with patch(
            "app.api.factor_lab.history_api.upsert_champions_fifo",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/factor-lab/history",
                    json={"symbol": "rb2610", "champions": [_champ([0])]},
                )
                assert resp.status_code == 500
        rollback_mock.assert_awaited_once()
        commit_mock.assert_not_awaited()
    finally:
        _clear_overrides()


async def test_save_history_logs_trial_best_effort():
    """trials/bars 齐备时登记试验(source=local);缺失时不登记"""
    user = _make_user()
    app = _get_test_app(user)
    try:
        with (
            patch(
                "app.services.factor_lab.persist.trial_svc.log_search_trial",
                new_callable=AsyncMock,
            ) as mock_trial,
            patch(
                "app.api.factor_lab.history_api.upsert_champions_fifo",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/factor-lab/history",
                    json={
                        "symbol": "rb2610",
                        "champions": [_champ([0])],
                        "trials": 5200,
                        "bars": 1215,
                        "config": {"population": 100, "generations": 52},
                    },
                )
                assert resp.status_code == 200
        mock_trial.assert_awaited_once()
        kw = mock_trial.await_args.kwargs
        assert kw["source"] == "local"
        assert kw["trials"] == 5200
        assert kw["bars"] == 1215
        assert kw["config"] == {"population": 100, "generations": 52}

        # 不带 trials → 不登记
        mock_trial.reset_mock()
        with patch(
            "app.api.factor_lab.history_api.upsert_champions_fifo",
            new_callable=AsyncMock,
            return_value=1,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/factor-lab/history",
                    json={"symbol": "rb2610", "champions": [_champ([0])]},
                )
                assert resp.status_code == 200
        mock_trial.assert_not_awaited()
    finally:
        _clear_overrides()


# ============ 服务层语义(FakeSession + 真实 upsert/list) ============


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
    """内存版 AsyncSession:解析 history_svc 用到的简单等值 select"""

    def __init__(self) -> None:
        self.rows: list[FactorHistory] = []
        self._pending: list[FactorHistory] = []

    @staticmethod
    def _where(stmt: Any) -> dict[str, Any]:
        wc = getattr(stmt, "whereclause", None)
        if wc is None:
            return {}
        return {
            c.left.key: getattr(c.right, "value", c.right)
            for c in getattr(wc, "clauses", [wc])
        }

    @staticmethod
    def _order_clauses(stmt: Any) -> list[tuple[str, bool]]:
        out: list[tuple[str, bool]] = []
        for ob in getattr(stmt, "_order_by_clauses", ()):
            if hasattr(ob, "element"):  # UnaryExpression(desc/asc)
                out.append((ob.element.key, True))
            else:
                out.append((ob.key, False))
        return out

    async def execute(self, stmt: Any) -> _FakeResult:
        await self.flush()
        entity = stmt.column_descriptions[0]["entity"]
        if entity is not FactorHistory:
            raise AssertionError(f"FakeSession 不支持 {entity}")
        flt = self._where(stmt)
        rows = [
            r for r in self.rows if all(getattr(r, k) == v for k, v in flt.items())
        ]
        for key, is_desc in reversed(self._order_clauses(stmt)):
            rows.sort(key=lambda r: getattr(r, key) or datetime.min, reverse=is_desc)
        limit = getattr(stmt, "_limit", None)
        return _FakeResult(rows[:limit] if limit else rows)

    def add(self, obj: Any) -> None:
        self._pending.append(obj)

    async def flush(self) -> None:
        for obj in self._pending:
            if obj.id is None:
                obj.id = uuid.uuid4()
            self.rows.append(obj)
        self._pending.clear()

    async def delete(self, obj: Any) -> None:
        await self.flush()
        self.rows.remove(obj)

    async def commit(self) -> None:
        await self.flush()

    async def rollback(self) -> None:
        self._pending.clear()


async def test_upsert_local_dedup_and_fifo():
    """真实 upsert_champions_fifo:source=local、同 tokens 去重更新、FIFO ≤10"""
    session = FakeSession()
    user_id = uuid4()
    payload = [
        _champ([3, 17, 5], 2.0, "ts_rank(close, 5)"),
        _champ([0], 1.5, "RET"),
        _champ([1, 2], 1.0, "ADD"),
    ]
    saved = await upsert_champions_fifo(
        session, user_id, "RB2610", "1d", payload, "local"
    )
    await session.commit()
    assert saved == 3

    items = await list_history(session, user_id, "rb2610")
    assert len(items) == 3
    assert all(i["source"] == "local" for i in items)
    assert {tuple(i["tokens"]) for i in items} == {(3, 17, 5), (0,), (1, 2)}

    # 同 tokens 二次保存:更新不新增,composite 已刷新
    payload[1]["composite"] = 9.9
    saved = await upsert_champions_fifo(
        session, user_id, "rb2610", "1d", payload, "local"
    )
    await session.commit()
    items = await list_history(session, user_id, "rb2610")
    assert len(items) == 3  # 条数不涨
    by_tokens = {tuple(i["tokens"]): i for i in items}
    assert by_tokens[(0,)]["composite"] == 9.9

    # FIFO:预插到满 10 条后保存新 tokens,最旧被挤出
    now = datetime.utcnow()
    for i in range(HISTORY_CAP - len(items)):
        session.add(
            FactorHistory(
                user_id=user_id,
                symbol="rb2610",
                timeframe="1d",
                tokens=[100 + i],
                tokens_key=f"100+{i}",
                text=f"old-{i}",
                composite=0.1,
                metrics={},
                source="local",
                created_at=now - timedelta(hours=HISTORY_CAP - i),
                updated_at=now - timedelta(hours=HISTORY_CAP - i),
            )
        )
    await session.commit()
    assert len(await list_history(session, user_id, "rb2610")) == HISTORY_CAP

    oldest = min(
        await list_history(session, user_id, "rb2610"),
        key=lambda i: i["updated_at"],
    )
    saved = await upsert_champions_fifo(
        session, user_id, "rb2610", "1d", [_champ([999], 3.0, "new")], "local"
    )
    await session.commit()
    assert saved == 1
    items = await list_history(session, user_id, "rb2610")
    assert len(items) == HISTORY_CAP  # 总数仍 ≤10
    assert all(i["id"] != oldest["id"] for i in items)  # 最旧被挤出
    assert any(tuple(i["tokens"]) == (999,) for i in items)  # 新的进入


async def test_upsert_local_symbol_normalized():
    """symbol 大小写/空白规整后按同一 key 存取"""
    session = FakeSession()
    user_id = uuid4()
    saved = await upsert_champions_fifo(
        session, user_id, " RB2610 ", "1d", [_champ([0], 1.0)], "local"
    )
    await session.commit()
    assert saved == 1
    items = await list_history(session, user_id, "rb2610")
    assert len(items) == 1
    assert items[0]["symbol"] == "rb2610"
