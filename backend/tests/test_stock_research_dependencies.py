from datetime import date

import pandas as pd
import pytest
from sqlalchemy import event, text

from app.services.stock_factors.engine import ResearchDataError
from tests.test_stock_research_snapshots import snapshot_database


def resolve(*args, **kwargs):
    from app.services.stock_factors.dependencies import resolve_dependencies
    return resolve_dependencies(*args, **kwargs)


@pytest.mark.parametrize("factors,expected_val,expected_fin", [
    (["ret_20d", "vol_20d"], (), ()),
    (["pe_ttm"], ("pe_ttm",), ()),
    (["pb", "turnover_20d"], ("pb", "turnover_rate"), ()),
    (["roe", "netprofit_yoy"], (), ("roe", "netprofit_yoy")),
])
def test_dependencies_are_explicit_stable_and_only_required_fields(factors, expected_val, expected_fin):
    plan = resolve(factors)
    assert plan.valuation_fields == expected_val and plan.financial_fields == expected_fin
    assert plan.panel_cells(100, 80, True) == 100 + len(expected_val) * 80 + len(expected_fin) * 100
    assert plan.manifest() == resolve(list(reversed(factors))).manifest()


def test_market_cap_dependencies_and_full_legacy_default():
    assert resolve(["ret_20d"], mktcap_neutral=True).valuation_fields == ("total_mv",)
    assert resolve(["ret_20d"], weight="mktcap").valuation_fields == ("total_mv",)
    assert resolve(["ret_20d"], weight="factor").valuation_fields == ()
    assert resolve().panel_cells(100, 80, True) == 820


@pytest.mark.parametrize("factors,kwargs", [([], {}), (["wrong"], {}), (["roe", "roe"], {}), ("roe", {}), (["roe"], {"mktcap_neutral": "false"}), (["roe"], {"weight": "unknown"})])
def test_invalid_dependency_selection_is_not_silently_downgraded(factors, kwargs):
    with pytest.raises(ResearchDataError) as error:
        resolve(factors, **kwargs)
    assert error.value.code == "invalid_research_dependencies" and error.value.status == 422


@pytest.mark.asyncio
async def test_price_only_snapshot_skips_auxiliary_tables_and_retains_price_cells(monkeypatch):
    from app.services.stock_factors import snapshots
    monkeypatch.setattr(snapshots, "MAX_PANEL_CELLS", 5)
    async with snapshot_database(missing_price=True) as (factory, database):
        queries = []
        event.listen(database, "before_cursor_execute", lambda connection, cursor, statement, *args: queries.append(statement))
        market, manifest = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["ret_20d"])
    assert market.valuation == {} and market.financial == {}
    assert manifest["coverage"]["panel_cells"] == 4 and manifest["coverage"]["missing_price_cells"] == 1
    assert manifest["snapshot_version"] == "stock-local-snapshot-v2-selective"
    assert not any("FROM stock_valuation" in query or "FROM stock_financial" in query for query in queries)


@pytest.mark.asyncio
async def test_selective_panels_match_full_and_fingerprints_bind_actual_fields():
    from app.services.stock_factors.snapshots import load_versioned_market_data
    async with snapshot_database() as (factory, database):
        full, original = await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3))
        selected, manifest = await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["roe"], mktcap_neutral=True)
        pd.testing.assert_frame_equal(full.financial["roe"], selected.financial["roe"])
        pd.testing.assert_frame_equal(full.valuation["total_mv"], selected.valuation["total_mv"])
        assert set(selected.financial) == {"roe"} and set(selected.valuation) == {"total_mv"}
        assert manifest["coverage"]["panel_cells"] == 12 and original["coverage"]["panel_cells"] == 36
        _, momentum = await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["ret_20d"])
        _, volatility = await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["vol_20d"])
        assert momentum["data_version"] == volatility["data_version"] != manifest["data_version"]
        with database.begin() as connection:
            connection.execute(text("UPDATE stock_valuation SET pb=1e999"))
        _, unchanged = await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["ret_20d"])
        assert unchanged["data_version"] == momentum["data_version"]
        with pytest.raises(ResearchDataError, match="非有限"):
            await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["pb"])


@pytest.mark.asyncio
@pytest.mark.parametrize("factors,options,expected", [(["ret_20d"], {}, 4), (["pb"], {}, 8), (["roe"], {}, 8), (["roe"], {"mktcap_neutral": True}, 12), (["ret_20d"], {"weight": "mktcap"}, 8)])
async def test_coverage_v2_uses_exact_same_dependency_and_cell_contract(factors, options, expected):
    from app.services.stock_factors.coverage import get_research_coverage
    from app.services.stock_factors.snapshots import load_versioned_market_data
    async with snapshot_database() as (factory, database):
        result = await get_research_coverage(factory, date(2024, 1, 2), date(2024, 1, 3), today=date(2024, 1, 3), factor_ids=factors, **options)
        market, manifest = await load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=factors, **options)
        assert result["coverage_version"] == "stock-research-coverage-v2"
        assert result["dependencies"] == manifest["dependencies"]
        assert result["selected"]["panel_cells"] == manifest["coverage"]["panel_cells"] == expected
        assert result["selected"]["valuation_rows"] == (4 if resolve(factors, **options).valuation_fields else 0)
        assert result["selected"]["financial_rows"] == (2 if resolve(factors, **options).financial_fields else 0)


@pytest.mark.asyncio
async def test_coverage_v2_HTTP_is_authenticated_bounded_and_rejects_unknown_dependencies():
    from types import SimpleNamespace
    from uuid import uuid4
    import httpx
    from fastapi import FastAPI
    from app.api.stock_research_jobs import router
    from app.core.deps import AuthContext, get_auth_context
    async with snapshot_database() as (factory, database):
        app = FastAPI()
        app.include_router(router)
        app.state.session_factory = factory
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = "/api/stock/research/coverage-v2"
            assert (await client.get(path + "?factor_ids=ret_20d")).status_code == 401
            app.dependency_overrides[get_auth_context] = lambda: AuthContext(SimpleNamespace(id=uuid4(), market_type="stock"), "virtual")
            for suffix in ("", "?factor_ids=invalid", "?factor_ids=roe&factor_ids=roe", "?factor_ids=roe&weight=invalid", "?factor_ids=roe&mktcap_neutral=wrong"):
                assert (await client.get(path + suffix)).status_code == 422
            result = await client.get(path + "?factor_ids=ret_20d&start_date=2024-01-02&end_date=2024-01-03")
            assert result.status_code == 200 and result.json()["selected"]["panel_cells"] == 4
            assert result.headers["X-Request-ID"] and result.headers["Cache-Control"] == "no-store"
            old = await client.get("/api/stock/research/coverage?start_date=2024-01-02&end_date=2024-01-03")
            assert old.json()["coverage_version"] == "stock-research-coverage-v1" and old.json()["selected"]["panel_cells"] == 36 and "dependencies" not in old.json()


@pytest.mark.asyncio
async def test_selective_reader_method_version_is_bound_into_input_fingerprint(monkeypatch):
    from app.services.stock_factors import snapshots
    async with snapshot_database() as (factory, database):
        _, before = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["ret_20d"])
        monkeypatch.setattr(snapshots, "SELECTIVE_SNAPSHOT_VERSION", "test-reader-next-version", raising=False)
        _, changed = await snapshots.load_versioned_market_data(factory, date(2024, 1, 2), date(2024, 1, 3), factor_ids=["ret_20d"])
        assert changed["snapshot_version"] == "test-reader-next-version"
        assert before["data_version"] != changed["data_version"]
