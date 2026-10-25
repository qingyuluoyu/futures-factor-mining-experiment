import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from tests.test_stock_raw_panel import NOW, record


class Records:
    def __init__(self, values):
        self.values = values
    def all(self):
        return self.values
    def mappings(self):
        return self


@pytest.mark.asyncio
async def test_original_raw_reader_moves_cpu_validation_off_request_loop(monkeypatch):
    from app.services import stock_raw_panel as module
    source, batch, rows = record()
    for item in rows:
        item["batch_id"] = batch.id
    class Session:
        calls = 0
        async def scalars(self, statement):
            self.calls += 1
            return Records([batch] if self.calls == 1 else [source])
        async def execute(self, statement):
            return Records(rows)
    original = module.build_raw_panel
    workers = []
    def build(*args, **kwargs):
        workers.append(threading.get_ident())
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "build_raw_panel", build)
    result = await module.read_raw_panel(Session(), [batch.version], ["600001.SH"], now=NOW)
    assert result["observations"][0]["volume"] == "0.01"
    assert workers and workers[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_unknown_pinned_version_has_typed_error_and_never_uses_latest():
    from app.services.stock_raw_panel import RawPanelVersionNotFound, read_raw_panel
    class Session:
        async def scalars(self, statement):
            return Records([])
        async def execute(self, statement):
            raise AssertionError("must not load source/observations after version miss")
    with pytest.raises(RawPanelVersionNotFound):
        await read_raw_panel(Session(), ["a" * 64], ["000001.SZ"], now=NOW)


def app_fixture(monkeypatch):
    from app.api import stock_archive_catalog as module
    from app.core.deps import get_current_user, get_session
    app = FastAPI(); app.include_router(module.router, prefix="/api/stock/market"); app.state.stock_archive_ready = True
    user = SimpleNamespace(id=uuid4(), market_type="stock")
    app.dependency_overrides[get_current_user] = lambda: user
    async def session():
        yield object()
    app.dependency_overrides[get_session] = session
    monkeypatch.setattr(module, "observation_gate", asyncio.BoundedSemaphore(2))
    return module, app, user


@pytest.mark.asyncio
async def test_observation_route_validates_pinned_identity_and_error_classes(monkeypatch):
    from app.services.stock_raw_panel import RawPanelBusy, RawPanelVersionNotFound
    module, app, user = app_fixture(monkeypatch)
    calls, failures = [], []
    async def read(session, versions, instruments, *, now):
        calls.append((versions, instruments))
        if failures:
            raise failures.pop(0)
        return {"source_calls": 0, "model_calls": 0}
    monkeypatch.setattr(module, "read_raw_panel", read)
    path = "/api/stock/market/archive/versions/" + "a" * 64 + "/observation"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for params in ({}, {"instrument_id": "000001"}, {"instrument_id": "000001.SH"}, {"instrument_id": "600001.SZ"}, {"instrument_id": "000001.SZ", "versions": "b" * 64}):
            result = await client.get(path, params=params)
            assert result.status_code == 422
            assert result.headers["Cache-Control"] == "no-store" and result.headers["X-Request-ID"]
        assert not calls
        assert (await client.get(path.replace("a" * 64, "latest"), params={"instrument_id": "000001.SZ"})).status_code == 422
        app.state.stock_archive_ready = False
        assert (await client.get(path, params={"instrument_id": "000001.SZ"})).status_code == 503 and not calls
        app.state.stock_archive_ready = True
        for error, status in ((RawPanelVersionNotFound("private"), 404), (RawPanelBusy("private"), 429), (ValueError("private"), 409), (RuntimeError("private"), 503), (TimeoutError("private"), 504)):
            failures.append(error)
            result = await client.get(path, params={"instrument_id": "000001.SZ"})
            assert result.status_code == status and "private" not in result.text
        user.market_type = "futures"
        assert (await client.get(path, params={"instrument_id": "000001.SZ"})).status_code == 409


@pytest.mark.asyncio
async def test_observation_concurrency_rejects_third_read_without_queue_or_retry(monkeypatch):
    module, app, user = app_fixture(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def read(session, versions, instruments, *, now):
        calls.append(versions)
        if len(calls) == 2:
            entered.set()
        await release.wait()
        return {"source_calls": 0, "model_calls": 0}
    monkeypatch.setattr(module, "read_raw_panel", read)
    path = "/api/stock/market/archive/versions/" + "a" * 64 + "/observation"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = asyncio.create_task(client.get(path, params={"instrument_id": "000001.SZ"}))
        second = asyncio.create_task(client.get(path, params={"instrument_id": "000001.SZ"}))
        await asyncio.wait_for(entered.wait(), 2)
        third = await client.get(path, params={"instrument_id": "000001.SZ"})
        assert third.status_code == 429 and len(calls) == 2
        release.set()
        assert [response.status_code for response in await asyncio.gather(first, second)] == [200, 200]
        assert not module.observation_gate.locked()


@pytest.mark.asyncio
async def test_cancelled_readers_do_not_release_cpu_slots_before_threads_exit(monkeypatch):
    from app.services import stock_raw_panel as module
    source, batch, rows = record()
    for item in rows:
        item["batch_id"] = batch.id
    class Session:
        calls = 0
        async def scalars(self, statement):
            self.calls += 1
            return Records([batch] if self.calls == 1 else [source])
        async def execute(self, statement):
            return Records(rows)
    original = module.build_raw_panel
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    guard = threading.Lock()
    count = 0
    remaining = 2
    def blocking(*args, **kwargs):
        nonlocal count, remaining
        with guard:
            count += 1
            position = count
            if count == 2:
                entered.set()
        if position <= 2:
            release.wait(5)
        try:
            return original(*args, **kwargs)
        finally:
            if position <= 2:
                with guard:
                    remaining -= 1
                    if remaining == 0:
                        finished.set()
    monkeypatch.setattr(module, "build_raw_panel", blocking)
    first = asyncio.create_task(module.read_raw_panel(Session(), [batch.version], ["600001.SH"], now=NOW))
    second = asyncio.create_task(module.read_raw_panel(Session(), [batch.version], ["600001.SH"], now=NOW))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel(); second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        failure = None
        try:
            await module.read_raw_panel(Session(), [batch.version], ["600001.SH"], now=NOW)
        except ValueError as error:
            failure = error
        assert count == 2, "cancelled awaiters must not permit a third CPU audit"
        assert type(failure).__name__ == "RawPanelBusy"
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
        await asyncio.gather(first, second, return_exceptions=True)
    result = await module.read_raw_panel(Session(), [batch.version], ["600001.SH"], now=NOW)
    assert result["observations"][0]["volume"] == "0.01"
