from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.stock_research_jobs import CreateResearchJob, prepare_job_parameters


def payload(kind="analysis", parameters=None, **extra):
    return {"kind": kind, "idempotency_key": str(uuid4()), "parameters": parameters if parameters is not None else {"factor_id": "ret_20d", "start_date": "20260713", "end_date": "20260911"}, **extra}


def test_shared_schema_keeps_existing_api_contract_and_normalizes_new_jobs():
    from app.api.stock_factors import AnalysisBody
    from app.schemas.stock_research import AnalysisBody as SharedAnalysisBody

    assert AnalysisBody is SharedAnalysisBody
    job = TypeAdapter(CreateResearchJob).validate_python(payload())
    parameters, digest = prepare_job_parameters(job, today=date(2026, 10, 18))
    assert parameters["start_date"] == "2026-07-13" and parameters["end_date"] == "2026-09-11"
    assert len(digest) == 64
    alternative = TypeAdapter(CreateResearchJob).validate_python(payload(parameters={"factor_id": "ret_20d", "start_date": "2026-07-13", "end_date": "2026/09/11"}))
    assert prepare_job_parameters(alternative, today=date(2026, 10, 18))[1] == digest


@pytest.mark.parametrize("change", [
    {"kind": "ai_trade"}, {"user_id": str(uuid4())}, {"trading_mode": "virtual"},
    {"parameters": {"factor_id": "ret_20d", "sql": "SELECT *"}},
    {"parameters": {"factor_id": "unknown"}},
    {"parameters": {"factor_ids": ["ret_20d"]}},
    {"title": ""},
    {"title": "   "},
    {"parameters": {"factor_id": "ret_20d", "preprocess": {"unknown_option": True}}},
])
def test_job_contract_rejects_privilege_fields_unknown_parameters_and_invalid_kind(change):
    with pytest.raises(ValidationError):
        TypeAdapter(CreateResearchJob).validate_python({**payload(), **change})


def test_empty_end_freezes_on_creation_but_retry_fingerprint_does_not_change_at_midnight():
    job = TypeAdapter(CreateResearchJob).validate_python(payload(parameters={"factor_id": "ret_20d", "start_date": "2026-07-13", "end_date": ""}))
    first, digest = prepare_job_parameters(job, today=date(2026, 10, 18))
    second, repeated = prepare_job_parameters(job, today=date(2026, 10, 19))
    assert first["end_date"] == "2026-10-18" and second["end_date"] == "2026-10-19"
    assert digest == repeated


def test_job_range_limits_do_not_silently_truncate_or_accept_future_dates():
    for start, end in [("20100101", "20261018"), ("20261018", "20990101")]:
        job = TypeAdapter(CreateResearchJob).validate_python(payload(parameters={"factor_id": "ret_20d", "start_date": start, "end_date": end}))
        with pytest.raises(ValueError):
            prepare_job_parameters(job, today=date(2026, 10, 18))
