from contextlib import asynccontextmanager
from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.stock import StockInstrument
from app.models.stock_eod import EodBase, StockEodBatch
from tests.test_stock_eod import catalog, row


@asynccontextmanager
async def database():
    engine = create_engine("sqlite:///:memory:")
    EodBase.metadata.create_all(engine)
    StockInstrument.__table__.create(engine)

    class Adapter:
        async def __aenter__(self):
            self.session = Session(engine, expire_on_commit=False)
            return self

        async def __aexit__(self, *args):
            self.session.close()

        def get_bind(self):
            return self.session.get_bind()

        def add(self, item):
            self.session.add(item)

        async def execute(self, statement, parameters=None):
            return self.session.execute(statement, parameters or {})

        async def scalar(self, statement):
            return self.session.scalar(statement)

        async def flush(self):
            self.session.flush()

        async def commit(self):
            self.session.commit()

    try:
        yield Adapter
    finally:
        engine.dispose()


class Source:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def fetch(self, trading_date, cached):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_eod_persists_catalog_and_partial_snapshot_once_without_poll_recharging():
    from app.services.stock_eod_sync import run_eod
    from app.services.stock_eod import eod_bars, latest_eod

    async with database() as factory:
        source = Source((catalog(), [row()], False))
        result = await run_eod(factory, source, date(2026, 10, 16))
        assert result["status"] == "partially_completed"
        assert (await run_eod(factory, source, date(2026, 10, 16)))["status"] == "already_recorded"
        assert source.calls == 1
        async with factory() as session:
            saved = await latest_eod(session, date(2026, 10, 18))
            assert saved.data_version == result["data_version"] and saved.attempts == 1
            assert saved.snapshot_json["missing_codes"] == ["600002"]
            assert (await session.scalar(select(StockInstrument).where(StockInstrument.code == "600001"))).name == "测试股"
            assert await eod_bars(session, "600001", date(2026, 10, 18), 250) == [{"time": "2026-10-16", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 123.45}]
            assert await eod_bars(session, "600002", date(2026, 10, 18), 250) == []
            assert await latest_eod(session, date(2026, 10, 15)) is None


def test_eod_bar_query_projects_one_security_in_database_instead_of_entire_snapshots():
    from sqlalchemy.dialects import postgresql, sqlite
    from app.services.stock_eod import eod_bar_statement

    postgres_sql = str(eod_bar_statement("600001", date(2026, 10, 18), 250, "postgresql").compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    sqlite_sql = str(eod_bar_statement("600001", date(2026, 10, 18), 250, "sqlite").compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "jsonb_path_query_first" in postgres_sql and "LIMIT 251" in postgres_sql
    assert "json_each" in sqlite_sql and "600001" in sqlite_sql
    with pytest.raises(ValueError):
        eod_bar_statement('"; arbitrary', date(2026, 10, 18), 250, "postgresql")


@pytest.mark.asyncio
async def test_eod_bad_next_day_cannot_replace_valid_snapshot_or_retry_source():
    from app.services.stock_eod_sync import EodSourceError, run_eod
    from app.services.stock_eod import latest_eod

    async with database() as factory:
        await run_eod(factory, Source((catalog(), [row()], False)), date(2026, 10, 16))
        source = Source((catalog(), [row(trade_date="20261017", close=99)], False))
        with pytest.raises(EodSourceError):
            await run_eod(factory, source, date(2026, 10, 17))
        assert (await run_eod(factory, source, date(2026, 10, 17)))["status"] == "already_recorded"
        assert source.calls == 1
        async with factory() as session:
            assert (await latest_eod(session, date(2026, 10, 18))).trade_date == date(2026, 10, 16)
            failed = await session.scalar(select(StockEodBatch).where(StockEodBatch.trade_date == date(2026, 10, 17)))
            assert failed.status == "failed" and failed.snapshot_json == {} and failed.finished_at


@pytest.mark.asyncio
async def test_eod_calendar_closed_is_not_a_zero_price_snapshot():
    from app.services.stock_eod_sync import run_eod
    from app.services.stock_eod import latest_eod

    async with database() as factory:
        source = Source()
        assert (await run_eod(factory, source, date(2026, 10, 17)))["status"] == "no_data"
        async with factory() as session:
            assert await latest_eod(session, date(2026, 10, 18)) is None
            saved = await session.scalar(select(StockEodBatch))
            assert saved.error_code == "market_closed" and saved.snapshot_json == {}


@pytest.mark.asyncio
async def test_eod_feature_market_and_kline_never_mix_raw_and_adjusted_series():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from app.api.stock import router as stock_router
    from app.api.stock_market import router as market_router
    from app.core.deps import get_current_user, get_session
    from app.models.kline_history import KlineHistory
    from app.services.stock_eod_sync import run_eod

    async with database() as factory:
        async with factory() as session:
            KlineHistory.__table__.create(session.get_bind())
        await run_eod(factory, Source((catalog(), [row(), row(ts_code="600002.SH", close=10)], False)), date(2026, 10, 16))
        async with factory() as session:
            session.add(KlineHistory(code="600001", market_type="stock", trade_date=date(2026, 10, 15), open=5, high=5, low=5, close=5, volume=1))
            await session.commit()
        app = FastAPI()
        app.state.stock_eod_enabled = True
        app.include_router(stock_router)
        app.include_router(market_router)
        async def dependency():
            async with factory() as session:
                yield session
        app.dependency_overrides[get_session] = dependency
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-eod", market_type="stock")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            boards = await client.get("/api/stock/market/boards")
            assert boards.status_code == 200 and boards.json()["coverage"]["quoted"] == 2
            assert boards.json()["price_adjustment"] == "none" and boards.json()["source_row_count"] == 2
            adjusted = (await client.get("/api/stock/kline?code=600001")).json()
            assert adjusted["price_adjustment"] == "qfq" and len(adjusted["bars"]) == 1 and adjusted["bars"][0]["close"] == 5
            raw = (await client.get("/api/stock/kline?code=600002")).json()
            assert raw["price_adjustment"] == "none" and raw["source"] == "teajoin_eod" and len(raw["bars"]) == 1
            app.state.stock_eod_enabled = False
            assert (await client.get("/api/stock/market/boards")).json()["coverage"]["quoted"] == 1
            assert (await client.get("/api/stock/kline?code=600002")).json()["bars"] == []


@pytest.mark.asyncio
async def test_eod_kline_failure_is_traced_sanitized_and_not_empty_prices():
    from types import SimpleNamespace
    from fastapi import FastAPI
    from app.api.stock import router
    from app.core.deps import get_current_user, get_session

    class BrokenSession:
        async def execute(self, statement):
            raise RuntimeError("private-database-payload")
    app = FastAPI()
    app.include_router(router)
    app.state.stock_eod_enabled = True
    app.dependency_overrides[get_session] = lambda: BrokenSession()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="qa-eod", market_type="stock")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        result = await client.get("/api/stock/kline?code=600001")
        assert result.status_code == 503 and result.headers.get("X-Request-ID")
        assert "private-database-payload" not in result.text and "bars" not in result.json()
