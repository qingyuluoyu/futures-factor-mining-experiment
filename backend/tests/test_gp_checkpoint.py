import copy
import json

import pytest
from app.services.factor_lab.checkpoint import canonical_bytes, digest

from app.services.factor_lab.search import SearchConfig, search_stepwise
from tests.test_factor_mining import _make_bars


def config(**changes):
    return SearchConfig(**{"population": 12, "generations": 7, "max_depth": 3, "top_n": 3, "seed": 7, "train_ratio": 0, "walk_forward_folds": 0, **changes})


@pytest.mark.parametrize("islands", [1, 2])
def test_each_generation_survives_json_checkpoint_and_restart_exactly(islands):
    bars, cfg = _make_bars(200, seed=2), config(islands=islands)
    full = list(search_stepwise(bars, "1d", cfg))
    assert len(full) == cfg.generations
    checkpoint = None
    for expected in full:
        generator = search_stepwise(bars, "1d", cfg, checkpoint=checkpoint)
        actual = next(generator)
        generator.close()
        assert actual.generation == expected.generation
        assert actual.champions == expected.champions
        checkpoint = json.loads(json.dumps(actual.checkpoint, allow_nan=False))
        assert checkpoint == expected.checkpoint
        assert checkpoint["generation"] == actual.generation
        assert sum(map(len, checkpoint["populations"])) == cfg.population
    assert list(search_stepwise(bars, "1d", cfg, checkpoint=checkpoint)) == []


def test_numeric_legacy_resume_replays_real_evolution_not_renamed_first_generation():
    bars, cfg = _make_bars(200, seed=3), config()
    full = list(search_stepwise(bars, "1d", cfg))
    resumed = list(search_stepwise(bars, "1d", cfg, start_generation=4))
    assert resumed == full[4:]


@pytest.mark.parametrize("change", ["price", "config", "hash", "generation", "rng", "population", "metrics"])
def test_checkpoint_rejects_changed_input_or_corrupted_state(change):
    bars, cfg = _make_bars(150, seed=4), config()
    first = next(search_stepwise(bars, "1d", cfg))
    checkpoint = copy.deepcopy(first.checkpoint)
    if change == "price":
        bars[0]["close"] += 1
    elif change == "config":
        cfg.cost += .001
    elif change == "hash":
        checkpoint["state_hash"] = "0" * 64
    elif change == "generation":
        checkpoint["generation"] = 2000
    elif change == "rng":
        checkpoint["rng_state"] = [3, [1], None]
    elif change == "population":
        checkpoint["populations"][0].append([0])
    else:
        checkpoint["best_seen"][0][2]["composite"] = float("nan")
    with pytest.raises(ValueError):
        next(search_stepwise(bars, "1d", cfg, checkpoint=checkpoint))


def test_snapshot_is_detached_and_checkpoint_cannot_mix_seed_best_or_wrong_generation():
    bars, cfg = _make_bars(150, seed=5), config()
    generator = search_stepwise(bars, "1d", cfg)
    first = next(generator)
    frozen = copy.deepcopy(first.checkpoint)
    next(generator)
    assert first.checkpoint == frozen
    for changes in [{"seed_best": [(1, [0], {})]}, {"start_generation": 3}]:
        with pytest.raises(ValueError):
            next(search_stepwise(bars, "1d", cfg, checkpoint=frozen, **changes))


def test_signed_zero_has_one_canonical_encoding_for_postgres_jsonb_roundtrip():
    assert canonical_bytes({"value": -0.0, "nested": [-0.0]}) == canonical_bytes({"value": 0.0, "nested": [0.0]})


def test_duplicate_history_is_losslessly_compacted_before_state_budget():
    cfg = config(population=200, generations=3, seed_tokens=[[0]] * 200, mutation_p=0, crossover_p=0)
    snapshots = list(search_stepwise(_make_bars(150, seed=2), "1d", cfg))
    assert len(snapshots) == 3
    for snapshot in snapshots:
        history = snapshot.checkpoint["best_seen"]
        assert len(history) == len({tuple(item[1]) for item in history}) == 1
        assert len(canonical_bytes(snapshot.checkpoint)) < 20000


@pytest.mark.parametrize("field", ["rng_state", "populations", "generation"])
def test_rehashed_but_structurally_invalid_checkpoint_is_rejected(field):
    bars, cfg = _make_bars(150, seed=2), config()
    checkpoint = next(search_stepwise(bars, "1d", cfg)).checkpoint
    checkpoint[field] = {"rng_state": [3, [1], None], "populations": [[[999999]]], "generation": -1}[field]
    checkpoint["state_hash"] = digest({key: value for key, value in checkpoint.items() if key != "state_hash"})
    with pytest.raises(ValueError):
        next(search_stepwise(bars, "1d", cfg, checkpoint=checkpoint))
