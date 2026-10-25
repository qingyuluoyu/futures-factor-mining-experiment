"""Package ⑤ red/green: evidence-graded qualification and per-action capabilities.

Every assertion here is about *derivation from evidence*.  No test may raise a grade
by passing a flag, an admin field or a hand-edited manifest: the only inputs are
rule-checkable evidence objects (verified calendar / historical universe / source
authorization / execution evidence) and the immutable fixed artifacts.
"""
from uuid import uuid4

import pytest

from tests.test_stock_research_archive_v2 import page, prepare, service
from tests.test_stock_research_job_service import job_database
from tests.test_stock_research_panels_v2 import snapshot

DAILY_VALUES = {"open": "11", "high": "13", "low": "10.5", "close": "12", "pre_close": "12", "volume": "1000", "amount": "12000", "adj_factor": "1"}
DAILY_UNITS = {"open": "CNY/share", "high": "CNY/share", "low": "CNY/share", "close": "CNY/share", "pre_close": "CNY/share",
    "volume": "share", "amount": "CNY", "adj_factor": "ratio"}


def complete_evidence(**changes):
    """A fully evidenced set: nothing missing, so the derived grade must be top."""
    from app.schemas.stock_dataset_inspection import SourceAuthorizationEvidence
    from app.schemas.stock_portfolio_v2 import ExecutionEvidence
    from app.services.stock_research_v2.point_in_time import TradingCalendar
    from app.services.stock_research_v2.universe import HistoricalUniverse, UniverseMembership
    value = {
        "calendar": TradingCalendar(start="2026-01-05", end="2026-01-06", sessions=["2026-01-05", "2026-01-06"], version="a" * 64),
        "universe": HistoricalUniverse(pool_id="qa-fixed", pool_version="a" * 64, start="2026-01-05", end="2026-01-06",
            members=[UniverseMembership(instrument_id="000001.SZ", valid_from="2026-01-05", valid_to=None, known_on="2026-01-05", source_sha256="c" * 64),
                UniverseMembership(instrument_id="600000.SH", valid_from="2026-01-05", valid_to=None, known_on="2026-01-05", source_sha256="d" * 64)]),
        "authorization": SourceAuthorizationEvidence(record="qa-authorized-source-batch", evidence_version="e" * 64, scope="backtest"),
        "execution": ExecutionEvidence(calendar_reference="qa:calendar", raw_prices_reference="qa:raw", status_and_limits_reference="qa:status",
            corporate_actions_reference="qa:actions", delistings_reference="qa:delistings", historical_universe_reference="qa:universe",
            coverage_start="2026-01-05", coverage_end="2026-01-06"),
    }
    value.update(changes)
    return value


def requirements(**changes):
    from app.schemas.stock_dataset_inspection import ResearchRequirements
    return ResearchRequirements.model_validate({**changes})


async def declared(archive):
    """A daily batch that declares its calendar/authorization/schema and the whole price grid."""
    rows = [{"instrument_id": instrument, "effective_date": day, "values": dict(DAILY_VALUES)}
        for instrument in ("000001.SZ", "600000.SH") for day in ("2026-01-05", "2026-01-06")]
    batch = page(collected_at="2026-01-06T08:00:00Z", calendar_reference="qa-exchange-calendar-v1",
        authorization_reference="qa-authorized-source-batch", source_schema_reference="qa-schema-v1",
        units=DAILY_UNITS, observations=rows)
    source = await archive.archive_page(batch, idempotency_key=uuid4(), request_id="a" * 32)
    await archive.session.commit()
    from app.schemas.stock_research_v2 import DatasetBuild
    ref = await archive.build_dataset(DatasetBuild(source_ids=[source.source_id], start="2026-01-05", end="2026-01-06",
        warmup_start="2026-01-05", pool_id="qa-fixed", pool_version="a" * 64, instruments=["000001.SZ", "600000.SH"]),
        idempotency_key=uuid4(), request_id="b" * 32)
    await archive.session.commit()
    return ref


async def financial_only(archive):
    """A consolidated financial batch with no daily source: no explicit price-adjustment basis."""
    rows = [{"instrument_id": instrument, "effective_date": "2026-01-05", "report_period": "2025-09-30",
        "announced_at": "2026-01-05T01:00:00Z", "available_at": "2026-01-05T01:00:00Z", "vintage": "original_filing",
        "vintage_reference": "qa-not-live-proof", "values": {"roe": "0.12"}} for instrument in ("000001.SZ", "600000.SH")]
    batch = page(kind="financial", frequency="disclosure", financial_basis="consolidated", price_adjustment="not_applicable",
        calendar_reference="qa-exchange-calendar-v1", authorization_reference="qa-authorized-source-batch",
        source_schema_reference="qa-schema-v1", units={"roe": "percent"}, observations=rows)
    source = await archive.archive_page(batch, idempotency_key=uuid4(), request_id="f" * 32)
    await archive.session.commit()
    from app.schemas.stock_research_v2 import DatasetBuild
    ref = await archive.build_dataset(DatasetBuild(source_ids=[source.source_id], start="2026-01-05", end="2026-01-06",
        warmup_start="2026-01-05", pool_id="qa-fixed", pool_version="a" * 64, instruments=["000001.SZ", "600000.SH"]),
        idempotency_key=uuid4(), request_id="e" * 32)
    await archive.session.commit()
    return ref


def capabilities(result):
    return {item["name"]: item for item in result["capabilities"]}


# --------------------------------------------------------------------------- #
# pure derivation: the grade is a function of the evidence/blocker set only
# --------------------------------------------------------------------------- #

def test_fully_evidenced_blocker_set_derives_backtest_ready():
    from app.services.stock_research_v2.qualification import derive_qualification
    assert derive_qualification([]) == "backtest_ready"


@pytest.mark.parametrize("reason", ["observation_only_dataset", "calendar_unverified", "historical_universe_unverified",
    "source_authorization_evidence_unverified", "selected_fields_unavailable", "historical_industry_unavailable"])
def test_any_observation_level_gap_caps_the_grade_at_observation_only(reason):
    from app.services.stock_research_v2.qualification import derive_qualification
    assert derive_qualification([reason]) == "observation_only"
    assert derive_qualification([reason, "execution_evidence_unavailable"]) == "observation_only"


@pytest.mark.parametrize("reason", ["execution_evidence_unavailable", "adjustment_basis_unverified",
    "price_volume_fields_unavailable", "corporate_action_economic_results_unavailable", "delisting_economic_results_unavailable"])
def test_backtest_prerequisite_gap_caps_the_grade_at_research_only(reason):
    from app.services.stock_research_v2.qualification import derive_qualification
    assert derive_qualification([reason]) == "research_only"


def test_reason_details_do_not_change_the_tier():
    from app.services.stock_research_v2.qualification import derive_qualification
    assert derive_qualification(["selected_fields_unavailable:roe"]) == "observation_only"
    assert derive_qualification(["price_volume_fields_unavailable:amount"]) == "research_only"


def test_unrecognised_blocker_reason_is_never_invented_as_a_pass():
    """未登记进 OBSERVATION_REASONS / RESEARCH_REASONS 的原因必须拒绝服务。

    实证过的缺口：``derive_qualification(["totally_unknown_reason"])`` 原本返回
    ``backtest_ready`` 且 ``allows(..., "analysis")`` 为 True——原因不在任何一个
    frozenset 里就被直接忽略，等于把未知阻断当成通过。当前 inspect_requirements
    只吐已知原因所以够不到，但本函数是四个门禁共享的唯一权威，capability_assessments
    也用构造列表调它；将来新增一个原因却忘了登记，就会静默放开资格。
    """
    from app.services.stock_research_v2.qualification import derive_qualification

    with pytest.raises(ValueError, match="unrecognised"):
        derive_qualification(["totally_unknown_reason"])
    with pytest.raises(ValueError, match="unrecognised"):
        derive_qualification(["calendar_unverified", "some_future_gate"])
    # ``:detail`` 后缀只指名，不改变前缀是否已登记
    with pytest.raises(ValueError, match="unrecognised"):
        derive_qualification(["some_future_gate:roe"])
    # 已知原因仍然正常分级，不受本次收紧影响
    assert derive_qualification(["selected_fields_unavailable:roe"]) == "observation_only"
    assert derive_qualification(["price_volume_fields_unavailable:amount"]) == "research_only"
    assert derive_qualification([]) == "backtest_ready"


def test_every_dialect_spelling_of_the_grade_check_reduces_to_one_canonical_set():
    """同一份 CHECK 的各方言渲染必须归一到同一个集合，否则 020 在 PostgreSQL 上永远 apply 不了。

    实证过的缺口：``grade_allowance`` 只认 SQLite 反射回来的裸表达式
    ``qualification IN (...)``。而 PostgreSQL 的 ``pg_get_constraintdef`` 吐出的是
    ``CHECK (qualification = ANY (ARRAY[...::text]))``——多一层 ``CHECK`` 外壳，
    又多一层 ``= ANY (ARRAY[`` 的左括号。归一化先删空白再替换，左括号留在了字符串
    开头，``fullmatch`` 从位置 0 就匹配不上，直接抛 "not an IN allowlist"。
    后果不是报错好看难看：``migrate_stock_research_qualification.validate_schema``
    拿反射文本和模型比对，比对不上就 MigrationError 回滚——**真实库是 PostgreSQL，
    等于 020 在生产上永远拒绝 apply**。这里把三种真实渲染都钉住。
    """
    from app.services.stock_research_v2.qualification import grade_allowance

    canonical = ("qualification", ("backtest_ready", "observation_only", "research_only"))
    spellings = (
        # SQLite 反射：裸表达式
        "qualification IN ('observation_only', 'research_only', 'backtest_ready')",
        # PostgreSQL pg_get_constraintdef：CHECK 外壳 + = ANY (ARRAY[...::text])
        "CHECK (qualification = ANY (ARRAY['observation_only'::text, 'research_only'::text, 'backtest_ready'::text]))",
        # pg_get_constraintdef 对多表达式约束会再包一层括号
        "CHECK ((qualification = ANY (ARRAY['observation_only'::text, 'research_only'::text, 'backtest_ready'::text])))",
        # 手写 DDL 原样
        "CHECK (qualification IN ('observation_only', 'research_only', 'backtest_ready'))",
    )
    for spelling in spellings:
        assert grade_allowance(spelling) == canonical, spelling
    # 剥外壳只能到「同一个集合」为止：等式约束、别的列、空白或非法白名单仍须拒绝，
    # 否则放宽解析等于给未登记的值开后门。
    for spelling in ("CHECK (qualification = 'observation_only')", "qualification = observation_only",
        "CHECK (other_column IN ('observation_only'))", "CHECK (qualification IN ())",
        "CHECK (qualification IN ('1bad', 'observation_only'))"):
        with pytest.raises(ValueError, match="allowlist"):
            grade_allowance(spelling)


def test_current_classification_or_unclear_adjustment_basis_never_reaches_backtest_ready():
    from app.services.stock_research_v2.qualification import derive_qualification
    assert derive_qualification(["adjustment_basis_unverified"]) == "research_only"
    assert derive_qualification(["historical_industry_unavailable"]) == "observation_only"


def test_there_is_no_manual_override_or_certification_entry_point():
    import inspect

    from app.services.stock_research_v2 import qualification
    for name in ("derive_qualification", "capability_assessments", "inspect_requirements"):
        function = getattr(qualification, name, None)
        assert callable(function), name
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {"override", "force", "certify", "certified", "admin", "approve", "qualification", "grade"}, name
    assert not [name for name in dir(qualification) if name.startswith(("set_", "override_", "certify_", "approve_", "force_"))]


# --------------------------------------------------------------------------- #
# integration: real fixed artifacts + optional certification evidence
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_known_qualified_request_reaches_backtest_ready_and_enables_research(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"], action="backtest"), **complete_evidence())
            assert result["qualification"] == "backtest_ready", result["blocking_reasons"]
            assert result["research_enabled"] is True
            assert result["blocking_reasons"] == []
            portfolio = capabilities(result)["portfolio"]
            assert portfolio["allowed_scope"] == "backtest_ready"
            assert set(portfolio["required_fields"]) == {"open", "high", "low", "close", "volume", "amount", "adj_factor"}


@pytest.mark.asyncio
async def test_missing_trading_calendar_caps_the_grade_at_observation_only(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"]), **complete_evidence(calendar=None))
            assert "calendar_unverified" in result["blocking_reasons"]
            assert result["qualification"] == "observation_only"
            assert result["research_enabled"] is False
            assert capabilities(result)["analysis"]["allowed_scope"] == "qa_only"


@pytest.mark.asyncio
async def test_missing_execution_evidence_caps_a_backtest_request_at_research_only(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"], action="backtest"), **complete_evidence(execution=None))
            assert "execution_evidence_unavailable" in result["blocking_reasons"]
            assert result["qualification"] == "research_only"
            portfolio = capabilities(result)["portfolio"]
            assert portfolio["allowed_scope"] == "qa_only"
            assert "execution_evidence_unavailable" in portfolio["missing_reasons"]


@pytest.mark.asyncio
async def test_unclear_adjustment_basis_caps_at_research_only(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await financial_only(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["roe"]), **complete_evidence())
            assert "adjustment_basis_unverified" in result["blocking_reasons"]
            assert result["qualification"] == "research_only"
            assert capabilities(result)["analysis"]["allowed_scope"] == "research_only"


@pytest.mark.asyncio
async def test_bare_observation_archive_is_never_upgraded_by_an_inspection(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref, source = await snapshot(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"]))
            assert result["qualification"] == "observation_only" and result["research_enabled"] is False
            assert "observation_only_dataset" in result["blocking_reasons"]
            for item in result["capabilities"]:
                assert item["allowed_scope"] in {"none", "qa_only"}


# --------------------------------------------------------------------------- #
# per-action dependency resolution
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_momentum_study_is_not_blocked_for_missing_financial_fields(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"]), **complete_evidence())
            analysis = capabilities(result)["analysis"]
            assert analysis["required_fields"] == ["close"]
            assert analysis["missing_reasons"] == []
            assert analysis["allowed_scope"] == "research_only"


@pytest.mark.asyncio
async def test_selected_feature_with_missing_data_fails_instead_of_being_dropped(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d", "roe"]), **complete_evidence())
            assert result["missing_fields"] == ["roe"] and result["required_fields"] == ["close", "roe"]
            for name in ("analysis", "ml"):
                item = capabilities(result)[name]
                assert "roe" in item["required_fields"], name
                assert "selected_fields_unavailable:roe" in item["missing_reasons"], name
                assert item["allowed_scope"] == "none", name


@pytest.mark.asyncio
async def test_industry_rotation_requires_historical_industry_but_plain_analysis_does_not(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            evidence = complete_evidence()
            rotation = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"], action="rotation"), **evidence)
            found = capabilities(rotation)
            assert "historical_industry_unavailable" in found["rotation"]["missing_reasons"]
            assert found["rotation"]["allowed_scope"] == "none"
            assert "industry" in found["rotation"]["required_fields"]
            assert "historical_industry_unavailable" not in found["analysis"]["missing_reasons"]
            plain = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"]), **evidence)
            assert "historical_industry_unavailable" not in plain["blocking_reasons"]
            assert "industry" not in capabilities(plain)["analysis"]["required_fields"]


@pytest.mark.asyncio
async def test_marketcap_weight_requires_marketcap_while_equal_weight_does_not(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            weighted = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"], weight="mktcap"), **complete_evidence())
            analysis = capabilities(weighted)["analysis"]
            assert "marketcap" in analysis["required_fields"]
            assert "selected_fields_unavailable:marketcap" in analysis["missing_reasons"]
            equal = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"], weight="equal"), **complete_evidence())
            assert "marketcap" not in capabilities(equal)["analysis"]["required_fields"]


@pytest.mark.asyncio
async def test_capability_contract_is_strictly_validated_and_ordered(tmp_path):
    from app.services.stock_research_v2.qualification import inspect_requirements
    async with job_database() as (factory, owner, other):
        await prepare(factory)
        async with factory() as session:
            archive = service(session, owner, tmp_path / "artifacts")
            ref = await declared(archive)
            result = await inspect_requirements(archive, ref, requirements(factor_ids=["ret_20d"]), **complete_evidence())
            assert [item["name"] for item in result["capabilities"]] == ["analysis", "portfolio", "ml", "rotation"]
            for item in result["capabilities"]:
                assert set(item) == {"name", "required_fields", "coverage", "missing_reasons", "allowed_scope", "limitations"}
                assert item["allowed_scope"] in {"none", "qa_only", "research_only", "backtest_ready"}
                for field, stats in item["coverage"].items():
                    assert set(stats) == {"raw_observations", "eligible_observations", "source_null", "excluded_domain",
                        "basis_unverified", "temporal_unverified"}
            from pydantic import ValidationError
            from app.schemas.stock_dataset_inspection import CapabilityAssessment
            with pytest.raises(ValidationError):
                CapabilityAssessment.model_validate({**result["capabilities"][0], "allowed_scope": "certified_by_admin"})
            with pytest.raises(ValidationError):
                CapabilityAssessment.model_validate({**result["capabilities"][0], "unknown_extra": 1})
