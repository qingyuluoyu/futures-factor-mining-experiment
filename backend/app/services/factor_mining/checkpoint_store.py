import json
import logging
import math
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.data.contracts import ALL_CONTRACTS, get_code_for_symbol
from app.models.gp_checkpoint import GpCheckpointSession
from app.services.factor_lab.checkpoint import binding, canonical_bytes, digest, restore_checkpoint
from app.services.factor_lab.search import SearchConfig, tokens_to_tree


logger = logging.getLogger("qihuo.gp_checkpoint")
MAX_TRIALS_PER_RUN = 5000


class CheckpointError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def validate_input(payload, cfg):
    try:
        if not isinstance(payload, dict) or set(payload) != {"symbol", "timeframe", "bars", "source_metadata"}:
            raise ValueError()
        symbol = payload["symbol"]
        matched = re.fullmatch(r"([a-z]{1,3})([0-9]{2})(0[1-9]|1[0-2])", symbol) if isinstance(symbol, str) else None
        if not matched or not get_code_for_symbol(symbol) or payload["timeframe"] != "1d":
            raise ValueError()
        bars, metadata = payload["bars"], payload["source_metadata"]
        required = {"supplier", "exchange", "currency", "price_unit", "volume_unit", "timezone", "calendar", "adjustment", "contract_basis", "source_version", "license_scope", "acquired_at"}
        if not isinstance(metadata, dict) or set(metadata) != required or any(not isinstance(value, str) or not 1 <= len(value) <= 160 for value in metadata.values()):
            raise ValueError()
        exchanges = {"上期所": "SHFE", "大商所": "DCE", "郑商所": "CZCE", "中金所": "CFFEX", "能源中心": "INE", "广期所": "GFEX"}
        exchange = next((exchanges.get(item["exchange"]) for item in ALL_CONTRACTS if item["code"] == get_code_for_symbol(symbol)), None)
        if not exchange or metadata["exchange"] != exchange or metadata["price_unit"] != "CNY_per_contract_unit" or metadata["currency"] != "CNY" or metadata["timezone"] != "Asia/Shanghai" or metadata["volume_unit"] != "contracts" or metadata["adjustment"] != "none" or metadata["contract_basis"] != "single_contract":
            raise ValueError()
        collected = datetime.fromisoformat(metadata["acquired_at"])
        if collected.tzinfo is None or collected > datetime.now(timezone.utc):
            raise ValueError()
        local_collected = collected.astimezone(ZoneInfo("Asia/Shanghai"))
        if not isinstance(bars, list) or not 80 <= len(bars) <= 8000:
            raise ValueError()
        previous = None
        for bar in bars:
            if not isinstance(bar, dict) or set(bar) != {"time", "open", "high", "low", "close", "volume"}:
                raise ValueError()
            stamp = date.fromisoformat(bar["time"])
            if len(bar["time"]) != 10 or (previous is not None and stamp <= previous) or stamp > local_collected.date() or (stamp == local_collected.date() and (local_collected.hour, local_collected.minute) < (16, 30)):
                raise ValueError()
            previous = stamp
            if any(type(bar[field]) not in (int, float) or not math.isfinite(bar[field]) for field in ("open", "high", "low", "close", "volume")):
                raise ValueError()
            if min(bar["open"], bar["high"], bar["low"], bar["close"]) <= 0 or bar["volume"] < 0 or bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]):
                raise ValueError()
        if not isinstance(cfg, SearchConfig) or any(type(getattr(cfg, field)) is not int for field in ("population", "generations", "max_depth", "top_n", "seed", "walk_forward_folds", "islands", "test_recent_bars")):
            raise ValueError()
        if not (10 <= cfg.population <= 200 and 1 <= cfg.generations <= 200 and 2 <= cfg.max_depth <= 6 and 1 <= cfg.top_n <= 100 and 1 <= cfg.islands <= 8 and 0 <= cfg.walk_forward_folds <= 6 and cfg.test_recent_bars >= 0):
            raise ValueError()
        if any(type(getattr(cfg, field)) not in (int, float) or not math.isfinite(getattr(cfg, field)) for field in ("cost", "train_ratio", "mutation_p", "crossover_p")) or not (0 <= cfg.cost <= 1 and 0 <= cfg.train_ratio < .95 and 0 <= cfg.mutation_p <= 1 and 0 <= cfg.crossover_p <= 1):
            raise ValueError()
        if type(cfg.live_fill_gate) is not bool or not 0 <= cfg.seed <= 4294967295 or cfg.test_recent_bars >= len(bars):
            raise ValueError()
        if cfg.cross_peers is not None and (not isinstance(cfg.cross_peers, list) or cfg.cross_peers):
            raise ValueError()
        if cfg.seed_tokens is not None and (not isinstance(cfg.seed_tokens, list) or len(cfg.seed_tokens) > 200 or any(
                not isinstance(tokens, list) or not 1 <= len(tokens) <= 1024 or any(type(token) is not int for token in tokens) or tokens_to_tree(tokens) is None for tokens in cfg.seed_tokens)):
            raise ValueError()
        if cfg.population * cfg.generations + len(cfg.seed_tokens or []) > MAX_TRIALS_PER_RUN:
            raise ValueError()
        encoded = canonical_bytes(payload)
        if len(encoded) > 4 * 1024 * 1024 or len(canonical_bytes(asdict(cfg))) > 64 * 1024:
            raise ValueError()
        return json.loads(encoded), json.loads(canonical_bytes(asdict(cfg)))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise CheckpointError("invalid_frozen_input", 422) from None


class CheckpointStore:
    def __init__(self, session, user_id, market_type, trading_mode):
        if not isinstance(user_id, UUID) or market_type != "futures" or trading_mode not in ("live", "virtual"):
            raise CheckpointError("unsupported_scope", 403)
        self.session, self.user_id, self.market, self.mode = session, user_id, market_type, trading_mode

    def scoped(self):
        return select(GpCheckpointSession).where(GpCheckpointSession.user_id == self.user_id, GpCheckpointSession.market_type == self.market, GpCheckpointSession.trading_mode == self.mode)

    async def create(self, idempotency_key, payload, cfg):
        if not isinstance(idempotency_key, UUID):
            raise CheckpointError("invalid_idempotency_key", 422)
        frozen, configuration = validate_input(payload, cfg)
        input_hash, config_hash = digest(frozen), digest(configuration)
        if self.session.get_bind().dialect.name == "postgresql":
            lock = int(digest([str(self.user_id), self.market, self.mode, str(idempotency_key)])[:15], 16)
            await self.session.execute(text("SET LOCAL lock_timeout = '3s'"))
            await self.session.execute(text("SET LOCAL statement_timeout = '10s'"))
            await self.session.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": lock})
        existing = await self.session.scalar(self.scoped().where(GpCheckpointSession.idempotency_key == idempotency_key).with_for_update())
        if existing is not None:
            if (existing.input_hash, existing.config_hash) != (input_hash, config_hash):
                raise CheckpointError("idempotency_conflict")
            return self.view(existing)
        now = datetime.now(timezone.utc)
        row = GpCheckpointSession(id=uuid4(), user_id=self.user_id, market_type=self.market, trading_mode=self.mode, idempotency_key=idempotency_key,
            input_json=frozen, input_hash=input_hash, config_json=configuration, config_hash=config_hash,
            checkpoint_json=None, checkpoint_hash=None, generation=0, revision=0, created_at=now, updated_at=now)
        self.session.add(row)
        await self.session.flush()
        logger.info("request_id=%s operation=gp_input_frozen user_id=%s mode=%s source_calls=0 model_calls=0", row.id.hex, self.user_id, self.mode)
        return self.view(row)

    async def load(self, identifier, *, lock=False):
        if not isinstance(identifier, UUID):
            raise CheckpointError("invalid_session_id", 422)
        query = self.scoped().where(GpCheckpointSession.id == identifier).execution_options(populate_existing=True)
        row = await self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise CheckpointError("not_found", 404)
        if digest(row.input_json) != row.input_hash or digest(row.config_json) != row.config_hash:
            raise CheckpointError("stored_input_corrupt")
        cfg = SearchConfig(**row.config_json)
        validate_input(row.input_json, cfg)
        if row.checkpoint_json is not None:
            try:
                state, _ = restore_checkpoint(row.checkpoint_json, binding(row.input_json["bars"], row.input_json["timeframe"], cfg), cfg)
                if state["generation"] != row.generation or state["state_hash"] != row.checkpoint_hash:
                    raise ValueError()
            except ValueError:
                raise CheckpointError("stored_checkpoint_corrupt") from None
        return row

    async def commit_generation(self, identifier, expected_revision, checkpoint):
        row = await self.load(identifier, lock=True)
        if type(expected_revision) is not int or row.revision != expected_revision:
            raise CheckpointError("stale_revision")
        cfg = SearchConfig(**row.config_json)
        try:
            state, _ = restore_checkpoint(checkpoint, binding(row.input_json["bars"], row.input_json["timeframe"], cfg), cfg)
        except ValueError:
            raise CheckpointError("invalid_checkpoint", 422) from None
        if state["generation"] != row.generation + 1:
            raise CheckpointError("invalid_generation_transition")
        row.checkpoint_json, row.checkpoint_hash = state, state["state_hash"]
        row.generation, row.revision, row.updated_at = state["generation"], row.revision + 1, datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("request_id=%s operation=gp_generation_commit revision=%s user_id=%s mode=%s", row.id.hex, row.revision, self.user_id, self.mode)
        return self.view(row)

    @staticmethod
    def view(row):
        return {"id": str(row.id), "generation": row.generation, "revision": row.revision, "input_hash": row.input_hash, "config_hash": row.config_hash, "checkpoint_hash": row.checkpoint_hash, "scope": {"market": row.market_type, "mode": row.trading_mode}, "source_certified": False, "production_worker_connected": False}
