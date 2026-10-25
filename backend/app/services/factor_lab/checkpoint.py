import hashlib
import json
import math
import random
import sys
from dataclasses import asdict

import numpy as np


VERSION = "gp-stepwise-checkpoint-v1"
ENVIRONMENT = f"python-{sys.version_info.major}.{sys.version_info.minor}/numpy-{np.__version__}"
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_TOKENS = 1024


def _number(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError("Unsupported checkpoint value")


def canonical_bytes(value):
    def normalized(item):
        if isinstance(item, np.generic):
            item = item.item()
        if isinstance(item, float) and item == 0:
            return 0.0
        if isinstance(item, dict):
            return {key: normalized(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalized(child) for child in item]
        return item
    try:
        return json.dumps(normalized(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=_number).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("Checkpoint contains invalid JSON or non-finite values") from None


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def binding(bars, timeframe, cfg):
    from .features import FEATURE_NAMES
    from .ops import OPS_CONFIG
    from .token_encoding import FEAT_OFFSET
    return digest({"bars": bars, "timeframe": timeframe, "config": asdict(cfg), "version": VERSION, "environment": ENVIRONMENT,
                   "registry": {"features": FEATURE_NAMES, "operators": [(name, arity) for name, operation, arity in OPS_CONFIG], "offset": FEAT_OFFSET}})


def compact_history(history):
    unique = {}
    for item in sorted(history, key=lambda item: item[0], reverse=True):
        unique.setdefault(tuple(item[1]), item)
    return list(unique.values())


def make_checkpoint(input_binding, generation, populations, rng, best_seen):
    payload = {"version": VERSION, "environment": ENVIRONMENT, "binding": input_binding,
               "generation": generation, "populations": populations, "rng_state": rng.getstate(), "best_seen": best_seen}
    encoded = canonical_bytes(payload)
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("Checkpoint exceeds 8 MiB state budget")
    payload = json.loads(encoded)
    payload["state_hash"] = digest(payload)
    return payload


def restore_checkpoint(payload, input_binding, cfg):
    from .search import tokens_to_tree

    if not isinstance(payload, dict) or set(payload) != {"version", "environment", "binding", "generation", "populations", "rng_state", "best_seen", "state_hash"}:
        raise ValueError("Checkpoint structure invalid")
    encoded = canonical_bytes(payload)
    if len(encoded) > MAX_STATE_BYTES or payload["version"] != VERSION or payload["environment"] != ENVIRONMENT or payload["binding"] != input_binding:
        raise ValueError("Checkpoint version, input, environment or budget mismatch")
    content = {key: value for key, value in payload.items() if key != "state_hash"}
    if payload["state_hash"] != digest(content) or type(payload["generation"]) is not int or not 1 <= payload["generation"] <= cfg.generations:
        raise ValueError("Checkpoint hash or generation invalid")
    state = json.loads(encoded)
    populations = state["populations"]
    islands = max(1, min(int(cfg.islands), cfg.population // 5))
    expected_sizes = [cfg.population // islands] * islands
    expected_sizes[-1] += cfg.population % islands
    if not isinstance(populations, list) or len(populations) != islands:
        raise ValueError("Checkpoint islands invalid")

    def valid_tokens(tokens):
        return isinstance(tokens, list) and 1 <= len(tokens) <= MAX_TOKENS and all(type(token) is int for token in tokens) and tokens_to_tree(tokens) is not None

    for population, size in zip(populations, expected_sizes):
        if not isinstance(population, list) or len(population) != size or not all(valid_tokens(tokens) for tokens in population):
            raise ValueError("Checkpoint population invalid")
    history = state["best_seen"]
    if not isinstance(history, list) or len(history) > 41000:
        raise ValueError("Checkpoint history budget invalid")
    for item in history:
        if not isinstance(item, list) or len(item) != 3 or type(item[0]) not in (int, float) or not math.isfinite(item[0]) or not valid_tokens(item[1]) or not isinstance(item[2], dict):
            raise ValueError("Checkpoint candidate invalid")
    rng_state = state["rng_state"]
    if (not isinstance(rng_state, list) or len(rng_state) != 3 or type(rng_state[0]) is not int or rng_state[0] != 3
            or not isinstance(rng_state[1], list) or len(rng_state[1]) != 625 or not all(type(item) is int for item in rng_state[1])
            or (rng_state[2] is not None and type(rng_state[2]) not in (int, float))):
        raise ValueError("Checkpoint random state invalid")
    rng = random.Random()
    try:
        rng.setstate((rng_state[0], tuple(rng_state[1]), rng_state[2]))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Checkpoint random state invalid") from None
    return state, rng
