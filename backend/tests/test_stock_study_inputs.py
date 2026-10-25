"""Actual local Parquet/registry QA. Dates/values are synthetic, not market certification."""
from contextlib import asynccontextmanager
from datetime import date
from uuid import uuid4

import pandas as pd
import pytest

from app.schemas.stock_research_v2 import DatasetBuild
from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import page, service
from tests.test_stock_columnar_registry import prepared
from tests.test_stock_research_job_service import job_database
from tests.test_stock_factor_values_v2 import frames


@asynccontextmanager
async def archived_inputs(tmp_path, *, mixed_prices=False, late_financial=False, vendor_basis=False, missing_disclosure=False):
    from app.services.stock_research_v2.columnar import ColumnarService
    from app.services.stock_research_v2.columnar_store import ParquetStore
    from app.services.stock_research_v2.point_in_time import TradingCalendar
    from app.services.stock_research_v2.universe import HistoricalUniverse
    data = frames()
    dates, codes = data["close"].index, data["close"].columns
    daily = [{"instrument_id": code, "effective_date": str(day.date()), "values": {"close": format(data["close"].loc[day, code], ".17f")}} for day in dates for code in codes]
    valuation = [{"instrument_id": code, "effective_date": str(day.date()), "values": {"pb": str(data["pb"].loc[day, code])}} for day in dates for code in codes]
    financial = [{"instrument_id": code, "effective_date": "2023-10-31", "report_period": "2023-09-30", "announced_on": "2023-10-30", "vintage": "original_filing", "vintage_reference": "qa-original-not-certified", "values": {"roe": str(data["roe"].iloc[0][code])}} for code in codes]
    if missing_disclosure:
        for row in financial:
            row.pop("announced_on"); row.pop("vintage_reference"); row["vintage"]="unverified"
    calendar = TradingCalendar(start="2023-10-01", end=str(dates[-1].date()), sessions=[day.date() for day in pd.bdate_range("2023-10-01", dates[-1])], version="c"*64)
    universe = HistoricalUniverse(pool_id="qa-original-pool", pool_version="d"*64, start=str(dates[0].date()), end=str(dates[-1].date()),
        members=[{"instrument_id": code, "valid_from": str(dates[10 if i == 0 else 0].date()), "valid_to": None, "known_on": str(dates[0].date()), "source_sha256": "e"*64} for i,code in enumerate(codes)])
    async with job_database() as (factory, owner, other):
        await prepared(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "sources")
            sources=[]
            pages=[page(stream_key="qa-prices", collected_at="2026-01-06T08:00:00Z", price_adjustment="qfq", adjustment_anchor=str(dates[-1].date()), units={"close":"CNY/share"}, observations=daily),
                page(stream_key="qa-valuation", collected_at="2026-01-06T08:00:00Z", kind="valuation", price_adjustment="not_applicable", units={"pb":"ratio"}, observations=valuation),
                page(stream_key="qa-financial", collected_at="2026-01-06T08:00:00Z", kind="financial", frequency="disclosure", financial_basis="vendor_reported" if vendor_basis else "consolidated", price_adjustment="not_applicable", units={"roe":"percent"}, observations=financial)]
            if mixed_prices:
                pages.append(page(stream_key="qa-raw-conflict", collected_at="2026-01-06T08:00:00Z", units={"close":"CNY/share"}, observations=daily[:1]))
            if late_financial:
                pages.append(page(stream_key="qa-new-report-missing-roe", collected_at="2026-01-06T08:00:00Z", kind="financial", frequency="disclosure", financial_basis="consolidated", price_adjustment="not_applicable", units={"gross_margin":"percent"}, observations=[
                    {"instrument_id":code,"effective_date":"2024-01-16","report_period":"2023-12-31","announced_on":"2024-01-15","vintage":"original_filing","vintage_reference":"qa-new-report-only","values":{"gross_margin":"11"}} for code in codes]))
            for source in pages:
                sources.append((await archive.archive_page(source,idempotency_key=uuid4(),request_id="a"*32)).source_id)
            reference=await archive.build_dataset(DatasetBuild(source_ids=sources,start=dates[0].date(),warmup_start=dates[0].date(),end=dates[-1].date(),pool_id=universe.pool_id,pool_version=universe.pool_version,instruments=list(codes)),idempotency_key=uuid4(),request_id="b"*32)
            target=tmp_path/"parquet"; target.mkdir()
            registry=ColumnarService(archive,ParquetStore(target))
            receipt=await registry.build(reference,idempotency_key=uuid4(),request_id="c"*32)
            await session.commit()
            yield registry,receipt,calendar,universe,data,other


@pytest.mark.asyncio
async def test_verified_parquet_to_calendar_grid_pit_and_historical_universe(tmp_path):
    from app.services.stock_research_v2.study_inputs import load_study_inputs
    async with archived_inputs(tmp_path) as (registry, receipt, calendar, universe, data, other):
        result=await load_study_inputs(registry,receipt,calendar=calendar,universe=universe,fields=["close","pb","roe"],request_id="d"*32)
        # Frequency annotations are not an exchange-calendar assertion; exact dates remain checked.
        pd.testing.assert_frame_equal(result.fields["close"],data["close"],rtol=1e-14,check_freq=False)
        assert result.fields["roe"].iloc[0,1] == data["roe"].iloc[0,1]
        assert not result.eligible.iloc[0,0] and result.eligible.iloc[10,0]
        assert result.metadata["qualification"] == "observation_only" and result.metadata["research_enabled"] is False
        assert result.metadata["price_adjustment"] == "qfq"
        assert result.metadata["adjustment_anchor"] == str(data["close"].index[-1].date())
        assert result.metadata["pit_method"] == "stock-disclosure-asof-v1"
        assert len(result.metadata["input_sha256"]) == 64
        from app.services.stock_research_v2.columnar import ColumnarService
        wrong=ColumnarService(service(registry.session,other,tmp_path/"sources"),registry.store)
        with pytest.raises(JobError) as denied:
            await load_study_inputs(wrong,receipt,calendar=calendar,universe=universe,fields=["close"],request_id="e"*32)
        assert denied.value.status == 404


@pytest.mark.asyncio
async def test_missing_calendar_pool_and_budget_never_guess_or_allocate_full_panel(tmp_path):
    from app.services.stock_research_v2.study_inputs import load_study_inputs
    async with archived_inputs(tmp_path) as (registry, receipt, calendar, universe, data, _):
        for patch in [{"calendar":None},{"max_cells":10},{"fields":["close","gross_margin"]},{"universe":universe.model_copy(update={"pool_version":"f"*64})}]:
            options={"calendar":calendar,"universe":universe,"fields":["close"],"request_id":"d"*32,**patch}
            with pytest.raises(JobError): await load_study_inputs(registry,receipt,**options)


@pytest.mark.asyncio
async def test_mixed_price_basis_is_blocked_before_study_even_when_prices_happen_to_match(tmp_path):
    with pytest.raises(JobError) as conflict:
        async with archived_inputs(tmp_path,mixed_prices=True):
            raise AssertionError("mixed basis must not reach an authorized study input")
    assert conflict.value.code == "mixed_adjustment_basis"


@pytest.mark.asyncio
async def test_projection_cannot_hide_newer_report_with_missing_selected_field(tmp_path):
    from app.services.stock_research_v2.study_inputs import load_study_inputs
    async with archived_inputs(tmp_path,late_financial=True) as (registry,receipt,calendar,universe,_,_):
        result=await load_study_inputs(registry,receipt,calendar=calendar,universe=universe,fields=["close","roe"],request_id="d"*32)
        assert result.fields["roe"].loc["2024-01-15"].notna().all()
        assert result.fields["roe"].loc["2024-01-16":].isna().all().all()


@pytest.mark.asyncio
async def test_vendor_reported_basis_is_a_business_error_not_a_raw_schema_exception(tmp_path):
    from app.services.stock_research_v2.study_inputs import load_study_inputs
    async with archived_inputs(tmp_path,vendor_basis=True) as (registry,receipt,calendar,universe,_,_):
        with pytest.raises(JobError) as unavailable:
            await load_study_inputs(registry,receipt,calendar=calendar,universe=universe,fields=["close","roe"],request_id="d"*32)
        assert unavailable.value.code == "study_financial_basis_unverified"


@pytest.mark.asyncio
async def test_legacy_missing_disclosure_time_is_a_sanitized_business_error(tmp_path):
    from app.services.stock_research_v2.study_inputs import load_study_inputs
    async with archived_inputs(tmp_path,missing_disclosure=True) as (registry,receipt,calendar,universe,_,_):
        with pytest.raises(JobError) as unavailable:
            await load_study_inputs(registry,receipt,calendar=calendar,universe=universe,fields=["close","roe"],request_id="d"*32)
        assert unavailable.value.code == "study_disclosure_unverified" and unavailable.value.status == 422
