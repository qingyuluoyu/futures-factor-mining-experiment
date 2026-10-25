from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.stock_replay import CreateReplayAccount, SubmitReplayOrder
from app.services.stock_replay import snapshot_bars, request_hash
from app.services.stock_replay_engine import ReplayError


def test_create_contract_rejects_fractional_cents_and_duplicate_datasets():
    dataset_id = uuid4()
    with pytest.raises(ValidationError):
        CreateReplayAccount(idempotency_key=uuid4(), name="账户", initial_cash="100.001", start_date="2026-09-01", dataset_ids=[dataset_id])
    with pytest.raises(ValidationError):
        CreateReplayAccount(idempotency_key=uuid4(), name="账户", initial_cash="10000", start_date="2026-09-01", dataset_ids=[dataset_id, dataset_id])


def test_order_contract_rejects_non_mainboard_and_fractional_lots():
    for code, quantity in [("rb2701", 100), ("300001", 100), ("000001", 1)]:
        with pytest.raises(ValidationError):
            SubmitReplayOrder(idempotency_key=uuid4(), expected_revision=0, code=code, direction="buy", quantity=quantity)


def test_snapshot_requires_exact_day_without_stale_fallback():
    bars = [{"trade_date": "2026-09-01", "open": "10", "high": "11", "low": "9", "close": "10", "volume_shares": 100000, "adj_factor": "1", "up_limit": "11", "down_limit": "9"}]
    assert snapshot_bars(bars, date(2026, 9, 1)).close == Decimal("10")
    with pytest.raises(ReplayError, match="缺少"):
        snapshot_bars(bars, date(2026, 9, 2))


def test_idempotency_hash_canonicalizes_decimal_and_key_order():
    assert request_hash({"cash": Decimal("100.00"), "name": "a"}) == request_hash({"name": "a", "cash": Decimal("100")})
