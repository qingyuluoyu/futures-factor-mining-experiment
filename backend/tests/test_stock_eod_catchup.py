from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.stock_eod import StockEodBatch
from tests.test_stock_eod_integration import database, Source


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_catchup_window_uses_shanghai_cutoff_not_host_date_or_weekday_guess():
    from app.services.stock_eod_sync import catchup_dates

    morning = catchup_dates(datetime(2026, 10, 20, 0, 30, tzinfo=SHANGHAI))
    assert morning == [date(2026, 10, 19 - offset) for offset in range(7)]
    assert catchup_dates(datetime(2026, 10, 20, 16, 30, tzinfo=SHANGHAI))[0] == date(2026, 10, 20)
    assert date(2026, 10, 18) in morning
    with pytest.raises(ValueError):
        catchup_dates(datetime(2026, 10, 20))


@pytest.mark.asyncio
async def test_restart_catches_missed_day_once_and_never_retries_failed_or_closed_dates():
    from app.services.stock_eod_sync import StockEodWorker

    now = datetime(2026, 10, 20, 0, 30, tzinfo=SHANGHAI)
    async with database() as factory:
        source = Source(result=None)
        worker = StockEodWorker(factory, source, catchup_enabled=True)
        first = await worker.run_once(now=now)
        assert first["trade_date"] == "2026-10-19" and first["status"] == "no_data"
        assert source.calls == 1
        restarted = StockEodWorker(factory, source, catchup_enabled=True)
        await restarted.run_once(now=now)
        assert source.calls == 2
        async with factory() as session:
            assert (await session.execute(select(StockEodBatch.trade_date).order_by(StockEodBatch.trade_date.desc()))).scalars().all() == [date(2026, 10, 19), date(2026, 10, 18)]
        failing = Source(error=ValueError("synthetic source failure"))
        with pytest.raises(ValueError):
            await StockEodWorker(factory, failing, catchup_enabled=True).run_once(now=now)
        assert failing.calls == 1
        for _ in range(4):
            await restarted.run_once(now=now)
        exhausted = await restarted.run_once(now=now)
        assert exhausted["status"] == "caught_up" and exhausted["source_calls"] == 0
        assert source.calls == 6 and failing.calls == 1


@pytest.mark.asyncio
async def test_sync_status_exposes_bounded_unverified_dates_not_fake_missing_trading_days():
    from app.services.stock_eod_status import read_sync_status

    async with database() as factory, factory() as session:
        result = await read_sync_status(session, now=datetime(2026, 10, 20, 0, 30, tzinfo=SHANGHAI), enabled=True, sync_enabled=True, worker_running=True)
    assert result["catchup"]["pending_dates"][0] == "2026-10-19"
    assert len(result["catchup"]["pending_dates"]) == 7
    assert result["catchup"]["calendar_verified"] is False
    assert result["catchup"]["max_dates_per_cycle"] == 1
    assert result["today_job"] is None and result["latest_snapshot"] is None


@pytest.mark.asyncio
async def test_catchup_requires_explicit_enablement_and_never_spends_overnight_by_default():
    from app.services.stock_eod_sync import StockEodWorker

    async with database() as factory:
        source = Source()
        result = await StockEodWorker(factory, source).run_once(now=datetime(2026, 10, 20, 0, 30, tzinfo=SHANGHAI))
        assert result["status"] == "before_window" and source.calls == 0
