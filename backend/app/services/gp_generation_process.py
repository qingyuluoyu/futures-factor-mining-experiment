import json
import os
import sys
import threading
from datetime import datetime, timezone


MAX_INPUT_BYTES = 13 * 1024 * 1024
MAX_OUTPUT_BYTES = 10 * 1024 * 1024 + 4096


def emit(message):
    from app.services.factor_lab.checkpoint import canonical_bytes
    encoded = canonical_bytes(message)
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("GP output budget")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def main():
    try:
        if len(sys.argv) != 2:
            return 2
        deadline = datetime.fromisoformat(sys.argv[1])
        if deadline.tzinfo is None:
            return 2
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return 124
    except (ValueError, TypeError):
        return 2
    watchdog = threading.Timer(min(300, remaining), lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        raw = sys.stdin.buffer.readline(MAX_INPUT_BYTES + 1)
        if not raw or len(raw) > MAX_INPUT_BYTES:
            raise ValueError("GP input budget")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"input", "config", "checkpoint", "generation", "deadline_at"}:
            raise ValueError("GP protocol")
        if datetime.fromisoformat(payload["deadline_at"]) != deadline:
            raise ValueError("GP deadline mismatch")
        from app.services.factor_lab.search import SearchConfig, search_stepwise
        from app.services.factor_mining.checkpoint_store import validate_input
        cfg = SearchConfig(**payload["config"])
        frozen, _ = validate_input(payload["input"], cfg)
        checkpoint = payload["checkpoint"]
        generation = payload["generation"]
        if type(generation) is not int or not 0 <= generation < cfg.generations or (generation != 0 and checkpoint is None):
            raise ValueError("GP generation invalid")
        generator = search_stepwise(frozen["bars"], frozen["timeframe"], cfg, start_generation=generation, checkpoint=checkpoint)
        snapshot = next(generator, None)
        generator.close()
        if snapshot is None:
            emit({"type": "error", "code": "no_qualifying_generation", "message": "本代没有合格候选，未伪造进度或冠军", "status": 422})
            return 1
        champions = [{"tokens": item.tokens, "text": item.text, "metrics": item.metrics, "composite": item.composite} for item in snapshot.champions]
        emit({"type": "result", "result": {"generation": snapshot.generation, "checkpoint": snapshot.checkpoint, "champions": champions}})
        return 0
    except Exception:
        emit({"type": "error", "code": "gp_process_invalid", "message": "单代输入、协议或计算异常；未保存结果且不自动重试", "status": 422})
        return 1
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
