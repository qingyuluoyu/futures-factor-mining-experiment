from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.stock_provider_contract import FetchBudget, ProviderRequest, BudgetError, authorize_attempt


def test_provider_identifier_dates_and_extra_keys_are_strict():
    request = ProviderRequest(kind="daily", instruments=["000001.SZ"], start="2025-01-01", end="2025-01-31")
    assert request.instruments == ["000001.SZ"]
    for values in [{"instruments": ["000001"]}, {"instruments": ["000001.SH"]}, {"instruments": ["000001.SZ"] * 2}, {"end": "2024-01-01"}, {"token": "not-accepted"}]:
        with pytest.raises(ValidationError): ProviderRequest(**{**request.model_dump(), **values})


def test_budget_defaults_to_zero_and_requires_known_incremental_cost():
    now = datetime.now(timezone.utc)
    budget = FetchBudget(deadline_at=now + timedelta(seconds=10))
    with pytest.raises(BudgetError): authorize_attempt(budget, attempts=0, rows=0, cost=Decimal(0), estimated_cost=Decimal(0), currency="CNY", now=now)
    budget = FetchBudget(max_requests=2, max_rows=100, max_cost="0.5", currency="CNY", deadline_at=now + timedelta(seconds=10))
    authorize_attempt(budget, attempts=0, rows=0, cost=Decimal("0.2"), estimated_cost=Decimal("0.3"), currency="CNY", now=now)
    for override in [{"attempts": 2}, {"rows": 100}, {"estimated_cost": None}, {"estimated_cost": Decimal("0.31")}, {"currency": "USD"}, {"now": now + timedelta(seconds=10)}, {"cost": Decimal("NaN")}, {"attempts": -1}]:
        with pytest.raises(BudgetError): authorize_attempt(budget, **{**dict(attempts=0, rows=0, cost=Decimal("0.2"), estimated_cost=Decimal("0.3"), currency="CNY", now=now), **override})


def test_budget_requires_timezone_and_finite_nonnegative_amounts():
    for values in [{"deadline_at": datetime(2025, 1, 1)}, {"deadline_at": datetime.now(timezone.utc), "max_cost": "Infinity"}, {"deadline_at": datetime.now(timezone.utc), "max_requests": True}]:
        with pytest.raises(ValidationError): FetchBudget(**values)


def test_benchmark_indices_do_not_use_equity_listing_prefix_rules():
    values = dict(instruments=["000300.SH", "399300.SZ"], start="2025-01-01", end="2025-01-31")
    assert ProviderRequest(kind="benchmark", **values).instruments == values["instruments"]
    with pytest.raises(ValidationError): ProviderRequest(kind="daily", **values)
