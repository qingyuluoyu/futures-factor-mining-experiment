from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from app.services.stock_factors.engine import PreprocessConfig
from app.services.stock_research_v2.expressions import compile_expression


def frames(days=90, stocks=30):
    dates = pd.bdate_range("2024-01-02", periods=days)
    codes = [f"{600000+i:06d}.SH" for i in range(stocks)]
    growth = np.linspace(0.0001, 0.004, stocks)
    close = pd.DataFrame(10 * np.cumprod(np.broadcast_to(1 + growth, (days, stocks)), axis=0), index=dates, columns=codes)
    def panel(values): return pd.DataFrame(np.broadcast_to(values, (days, stocks)).copy(), index=dates, columns=codes)
    return {"close": close, "pe_ttm": panel(np.linspace(30, 3, stocks)), "pb": panel(np.linspace(5, 1, stocks)),
        "roe": panel(np.linspace(1, 30, stocks)), "gross_margin": panel(np.linspace(10, 40, stocks)),
        "revenue_yoy": panel(np.linspace(-10, 30, stocks)), "netprofit_yoy": panel(np.linspace(-20, 40, stocks)),
        "turnover": panel(np.linspace(4, 0.5, stocks)), "marketcap": panel(np.linspace(1e8, 9e8, stocks))}


def custom(expression="roe / pb"):
    from app.services.stock_research_v2.factor_values import custom_factor
    compiled = compile_expression(expression, allowed_fields={"roe", "pb", "close"})
    record = {"id": uuid4(), "revision": 1, "latest_revision": 1, "name": "QA expression", "direction": "high", "description": "QA only",
        "expression": compiled.expression, "expression_version": compiled.version, "formula_version": "stock-expression-v1",
        "fields": list(compiled.fields), "lookback": compiled.lookback, "disabled": False, "created_at": "2026-10-25T00:00:00Z"}
    return custom_factor(record), record


def config(**patch):
    return PreprocessConfig(**{"winsorize": "none", "standardize": False, "fillna": False, **patch})


def test_all_nine_factors_reuse_their_formulas_and_custom_exact_version_enters_same_bundle():
    from app.services.stock_factors.catalog import FACTOR_BY_ID
    from app.services.stock_research_v2.factor_values import builtin_factor, compute_factors
    data = frames()
    fixed, _ = custom()
    bundle = compute_factors(data, [*[builtin_factor(key) for key in FACTOR_BY_ID], fixed], config=config())
    pd.testing.assert_frame_equal(bundle.values["ret_20d"], data["close"] / data["close"].shift(20) - 1)
    pd.testing.assert_frame_equal(bundle.values["vol_20d"], data["close"].pct_change(fill_method=None).rolling(20).std() * np.sqrt(244))
    pd.testing.assert_frame_equal(bundle.values["turnover_20d"], data["turnover"].rolling(20).mean())
    pd.testing.assert_frame_equal(bundle.values[fixed.key], data["roe"] / data["pb"])
    assert len(bundle.values) == 10 and bundle.validation_status == "calculation_only_unverified_inputs"
    assert bundle.definitions[-1].version == fixed.version


def test_invalid_valuation_domains_and_custom_arithmetic_cannot_be_restored_by_median_fill():
    from app.services.stock_research_v2.factor_values import builtin_factor, compute_factors
    data = frames(); data["pb"].iloc[0, :3] = [0, -1, np.nan]
    fixed, _ = custom()
    bundle = compute_factors(data, [builtin_factor("pb"), fixed], config=config(fillna=True))
    assert bundle.values["pb"].iloc[0, :3].isna().all()
    assert bundle.values[fixed.key].iloc[0, :3].isna().all()
    assert bundle.coverage["pb"]["excluded_domain"] == 2
    assert bundle.values[fixed.key].iloc[0, 3] == pytest.approx(data["roe"].iloc[0, 3] / data["pb"].iloc[0, 3])


def test_future_suffix_does_not_change_past_factors_and_mismatched_versions_or_axes_fail():
    from app.services.stock_research_v2.factor_values import builtin_factor, compute_factors, custom_factor
    data = frames(); factor = builtin_factor("ret_20d")
    before = compute_factors(data, [factor], config=config()).values[factor.key]
    data["close"].iloc[-1] *= 50
    after = compute_factors(data, [factor], config=config()).values[factor.key]
    pd.testing.assert_frame_equal(before.iloc[:-1], after.iloc[:-1])
    _, record = custom()
    with pytest.raises(ValueError): custom_factor({**record, "expression_version": "0" * 64})
    with pytest.raises(ValueError): compute_factors({**data, "roe": data["roe"].iloc[1:]}, [builtin_factor("roe")])
    with pytest.raises(ValueError): compute_factors(data, [factor], max_cells=1)


def test_historical_industry_and_universe_masks_are_applied_at_each_signal_date():
    from app.services.stock_research_v2.factor_values import builtin_factor, compute_factors
    data = frames(); template = data["close"]
    industry = pd.DataFrame("QA-A", index=template.index, columns=template.columns)
    industry.iloc[:45, :15] = "QA-B"; industry.iloc[45:, 15:] = "QA-B"
    active = pd.DataFrame(True, index=template.index, columns=template.columns); active.iloc[:30, -1] = False
    bundle = compute_factors(data, [builtin_factor("roe")], config=config(industry_neutral=True), industry=industry, eligible=active)
    assert bundle.values["roe"].iloc[:30, -1].isna().all()
    for position in [10, 60]:
        for group in ["QA-A", "QA-B"]:
            values = bundle.values["roe"].iloc[position].loc[industry.iloc[position].eq(group)].dropna()
            assert abs(values.mean()) < 1e-9
    with pytest.raises(ValueError): compute_factors(data, [builtin_factor("roe")], config=config(industry_neutral=True))


@pytest.mark.asyncio
async def test_saved_custom_revision_resolves_with_owner_permissions_then_enters_ic():
    from app.services.stock_research_v2.factor_values import resolve_factors, compute_factors
    from app.services.stock_research_v2.analysis import analyze_values
    from app.services.stock_research_v2.custom_factors import CustomFactorService
    from app.schemas.stock_custom_factor import CustomFactorCreate
    from app.services.stock_research_jobs import JobError
    from tests.test_stock_custom_factors_api import prepare
    from tests.test_stock_research_job_service import job_database
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            service = CustomFactorService(session, owner, "virtual")
            saved = await service.mutate(CustomFactorCreate(idempotency_key=uuid4(), name="QA quality/value", expression="roe / pb", direction="high"), request_id="a"*32)
            await session.commit()
            ref = {"id": saved["id"], "revision": saved["revision"], "expression_version": saved["expression_version"]}
            definitions = await resolve_factors(service, [], [ref])
            data = frames(); bundle = compute_factors(data, definitions, config=config())
            assert analyze_values(bundle.values[definitions[0].key], data["close"], horizon=3)["ic"]["rank_ic_mean"] > 0.9
            for denied in [CustomFactorService(session, other, "virtual"), CustomFactorService(session, owner, "live")]:
                with pytest.raises(JobError): await resolve_factors(denied, [], [ref])
