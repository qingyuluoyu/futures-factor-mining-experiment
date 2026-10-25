from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.factor_lab.search import search_stepwise
from app.services.factor_mining.checkpoint_store import CheckpointError, CheckpointStore
from app.models.gp_checkpoint import GpCheckpointBase
from tests.test_factor_mining import _make_bars
from tests.test_gp_checkpoint import config


def frozen_input():
    bars = _make_bars(150, seed=2)
    for offset, bar in enumerate(bars):
        bar["time"] = (date(2024, 1, 1) + timedelta(days=offset)).isoformat()
    return {"symbol": "rb2701", "timeframe": "1d", "bars": bars, "source_metadata": {
        "supplier": "synthetic-test-fixture", "exchange": "SHFE", "currency": "CNY", "price_unit": "CNY_per_contract_unit",
        "volume_unit": "contracts", "timezone": "Asia/Shanghai", "calendar": "synthetic-not-exchange-certification",
        "adjustment": "none", "contract_basis": "single_contract", "source_version": "fixture-v1",
        "license_scope": "test-fixture-only", "acquired_at": datetime.now(timezone.utc).isoformat()}}


@asynccontextmanager
async def storage():
    engine = create_engine("sqlite:///:memory:")
    GpCheckpointBase.metadata.create_all(engine)

    class Adapter:
        def __init__(self):
            self.session = Session(engine, expire_on_commit=False)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            self.session.close()
        def get_bind(self):
            return self.session.get_bind()
        async def scalar(self, statement):
            return self.session.scalar(statement)
        async def execute(self, statement, parameters=None):
            return self.session.execute(statement, parameters or {})
        def add(self, row):
            self.session.add(row)
        async def flush(self):
            self.session.flush()
        async def commit(self):
            self.session.commit()
        async def rollback(self):
            self.session.rollback()
    try:
        yield Adapter
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_frozen_input_scoped_idempotency_and_sequential_checkpoint_commit():
    owner, key, payload, cfg = uuid4(), uuid4(), frozen_input(), config(generations=3)
    async with storage() as factory, factory() as session:
        store = CheckpointStore(session, owner, "futures", "virtual")
        created = await store.create(key, payload, cfg)
        await session.commit()
        identifier = UUID(created["id"])
        assert (await store.create(key, payload, cfg))["id"] == created["id"]
        changed = {**payload, "symbol": "rb2702"}
        with pytest.raises(CheckpointError) as conflict:
            await store.create(key, changed, cfg)
        assert conflict.value.code == "idempotency_conflict"
        generator = search_stepwise(payload["bars"], "1d", cfg)
        first, second = next(generator), next(generator)
        with pytest.raises(CheckpointError):
            await store.commit_generation(identifier, 0, second.checkpoint)
        advanced = await store.commit_generation(identifier, 0, first.checkpoint)
        await session.commit()
        assert advanced["generation"] == advanced["revision"] == 1
        with pytest.raises(CheckpointError) as stale:
            await store.commit_generation(identifier, 0, first.checkpoint)
        assert stale.value.code == "stale_revision"
        saved = await store.load(identifier)
        assert saved.input_json == payload and saved.checkpoint_json == first.checkpoint
        for identity, market, mode in ((uuid4(), "futures", "virtual"), (owner, "futures", "live"), (owner, "stock", "virtual")):
            with pytest.raises(CheckpointError):
                await CheckpointStore(session, identity, market, mode).load(identifier)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["close", "volume", "time"])
async def test_missing_or_malformed_financial_input_is_not_filled_or_truncated(field):
    payload = frozen_input()
    payload["bars"][0].pop(field)
    async with storage() as factory, factory() as session:
        with pytest.raises(CheckpointError):
            await CheckpointStore(session, uuid4(), "futures", "virtual").create(uuid4(), payload, config())


@pytest.mark.asyncio
async def test_fractional_volume_is_preserved_and_transaction_rollback_keeps_previous_generation():
    payload, owner = frozen_input(), uuid4()
    payload["bars"][0]["volume"] = 123.45
    async with storage() as factory:
        async with factory() as session:
            store = CheckpointStore(session, owner, "futures", "virtual")
            created = await store.create(uuid4(), payload, config())
            await session.commit()
            identifier = UUID(created["id"])
            checkpoint = next(search_stepwise(payload["bars"], "1d", config())).checkpoint
            await store.commit_generation(identifier, 0, checkpoint)
            await session.rollback()
        async with factory() as session:
            saved = await CheckpointStore(session, owner, "futures", "virtual").load(identifier)
            assert saved.generation == 0 and saved.checkpoint_json is None
            assert saved.input_json["bars"][0]["volume"] == 123.45


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,exchange", [("rb", "SHFE"), ("rb888", "SHFE"), ("rb!2701", "SHFE"), ("rb2713", "SHFE"), ("rb2701", "NYSE")])
async def test_single_contract_metadata_requires_canonical_symbol_and_correct_exchange(symbol, exchange):
    payload = frozen_input()
    payload["symbol"], payload["source_metadata"]["exchange"] = symbol, exchange
    async with storage() as factory, factory() as session:
        with pytest.raises(CheckpointError):
            await CheckpointStore(session, uuid4(), "futures", "virtual").create(uuid4(), payload, config())


@pytest.mark.asyncio
async def test_new_persistent_runs_reject_excessive_per_run_work_before_computation():
    async with storage() as factory, factory() as session:
        with pytest.raises(CheckpointError):
            await CheckpointStore(session, uuid4(), "futures", "virtual").create(uuid4(), frozen_input(), config(population=200, generations=200))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"seed_tokens": ["bad"]}, {"seed_tokens": [[0.5]]}, {"live_fill_gate": "yes"}, {"cross_peers": False}])
async def test_checkpoint_configuration_does_not_silently_coerce_control_protocol(change):
    async with storage() as factory, factory() as session:
        with pytest.raises(CheckpointError):
            await CheckpointStore(session, uuid4(), "futures", "virtual").create(uuid4(), frozen_input(), config(**change))
