"""The research kernel IPC seam, exercised by a real subprocess and nothing else.

Everything else in the ④-3 tests reaches ``research_process`` in-process: the parent
side is checked against a fake ``spawn``, and the kernel table is called directly.
That leaves the one seam that actually ships untested — claim JSON on the child's
stdin, one message on its stdout — so this module runs the real program with the real
interpreter and asserts on what really crosses the pipe.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from app.services.stock_research_jobs import utc_now
from tests.test_stock_benchmark_v2 import index_series, portfolio
from tests.test_stock_research_v2_task_dispatch import research_claim

TASK_MODULE = "app.services.stock_research_v2.research_process"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def run_child(claim, root: Path):
    """Start the real program and return (returncode, parsed stdout message)."""
    environment = {**os.environ, "WENYUAN_STOCK_DATASET_ROOT": str(root)}
    # Native BLAS pools are pinned to one thread, exactly as the parent pins them.
    environment.update({name: "1" for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    child = subprocess.run([sys.executable, "-u", "-m", TASK_MODULE], cwd=BACKEND_ROOT, env=environment,
        input=claim.model_dump_json().encode("utf8") + b"\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    lines = [line for line in child.stdout.decode("utf8").splitlines() if line.strip()]
    assert len(lines) == 1, f"exactly one protocol message, got {len(lines)}: {child.stdout[:400]!r} / {child.stderr[-400:]!r}"
    return child.returncode, json.loads(lines[0])


def benchmark_claim(store, **overrides):
    """A real benchmark claim over a really stored strategy artifact."""
    stored = store.put_json(portfolio().model_dump(mode="json"))
    parameters = {"kind": "benchmark", "dataset": {"dataset_id": str(uuid4()), "data_version": "a" * 64,
        "qualification": "observation_only"}, "strategy_artifact_sha256": stored.sha256,
        "index": index_series().model_dump(mode="json")}
    parameters.update(overrides)
    return research_claim(kind="benchmark", parameters=parameters)


def test_the_child_computes_a_benchmark_report_and_reports_only_content(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / "artifacts")
    code, message = run_child(benchmark_claim(store), store.root)
    assert code == 0 and message["type"] == "result"
    outcome = message["result"]
    # The child names the kind it was given and the shape that kind may publish, and
    # nothing else: no job id, no scope, no artifact row, no experiment id.
    assert outcome["kind"] == "benchmark" and outcome["shape"] == "report"
    assert set(outcome) == {"kind", "shape", "report_sha256", "report_byte_size"}
    assert outcome["report_byte_size"] > 0
    # The digest it reported is readable by the parent, under the parent's own store.
    report = store.read_json(outcome["report_sha256"])
    assert report["validation_status"] == "qa_only"
    assert report["benchmark_metrics"]["periods_per_year"] == 244
    # A QA comparison never claims economic excess it cannot evidence.
    assert report["economic_excess_available"] is False


def test_the_child_reports_a_named_code_rather_than_crashing_when_evidence_is_missing(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / "artifacts")
    claim = benchmark_claim(store, strategy_artifact_sha256="f" * 64)
    before = {item.name for item in store.root.iterdir()}
    code, message = run_child(claim, store.root)
    assert code == 1 and message["type"] == "error"
    assert message["code"] == "task_inputs_unavailable" and message["status"] == 422
    # A refusal writes nothing new: an absent artifact cannot leave a plausible
    # report behind, and the strategy it was handed stays exactly as it was.
    assert {item.name for item in store.root.iterdir()} == before


def test_the_child_refuses_a_study_kind_by_name_and_names_the_missing_evidence(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from tests.test_stock_research_v2_task_dispatch import kind_parameters
    store = ArtifactStore(tmp_path / "artifacts")
    for kind in ("research_ic", "backtest", "ml_train", "report"):
        claim = research_claim(kind=kind, parameters={"kind": kind, **kind_parameters(kind)})
        before = {item.name for item in store.root.iterdir()}
        code, message = run_child(claim, store.root)
        assert code == 1 and message["type"] == "error", kind
        assert message["code"] == "task_inputs_unavailable" and message["status"] == 422, kind
        # The refusal has to say which evidence is absent, not just that something is.
        assert kind in message["message"] and "日历" in message["message"], kind
        assert {item.name for item in store.root.iterdir()} == before, kind


def test_the_child_refuses_an_expired_claim_before_running_any_kernel(tmp_path):
    from datetime import timedelta
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / "artifacts")
    expired = benchmark_claim(store)
    before = {item.name for item in store.root.iterdir()}
    expired = expired.model_copy(update={"deadline_at": (utc_now() - timedelta(seconds=5)).isoformat()})
    code, message = run_child(expired, store.root)
    assert code == 1 and message["code"] == "execution_timeout"
    assert {item.name for item in store.root.iterdir()} == before


def test_the_child_refuses_a_claim_whose_kind_has_no_module(tmp_path):
    from app.schemas.stock_research_v2_jobs import DatasetClaim
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / "artifacts")
    observation = DatasetClaim.model_validate({**research_claim().model_dump(mode="json"), "kind": "dataset",
        "parameters": {"source_ids": [str(uuid4())], "start": "2026-01-05", "end": "2026-01-05", "warmup_start": "2026-01-05",
            "pool_id": "qa", "pool_version": "a" * 64, "instruments": ["000001.SZ"]}})
    code, message = run_child(observation, store.root)
    assert code == 1 and message["code"] == "task_kind_unregistered"


def test_the_child_refuses_a_duplicated_ipc_field_rather_than_picking_one(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    store = ArtifactStore(tmp_path / "artifacts")
    # ``model_dump_json`` is compact, so the surgery lands on a real key.  A duplicated
    # top-level field must be refused: picking the last one would let a caller rename a
    # claim's kind after the parent had already fenced it.
    raw = benchmark_claim(store).model_dump_json()
    assert '"kind":"benchmark"' in raw, "the compact payload must contain the key this test duplicates"
    duplicated = raw.replace('"kind":"benchmark"', '"kind":"benchmark","kind":"report"', 1)
    assert duplicated.count('"kind"') == raw.count('"kind"') + 1
    environment = {**os.environ, "WENYUAN_STOCK_DATASET_ROOT": str(store.root)}
    child = subprocess.run([sys.executable, "-u", "-m", TASK_MODULE], cwd=BACKEND_ROOT, env=environment,
        input=duplicated.encode("utf8") + b"\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    lines = [line for line in child.stdout.decode("utf8").splitlines() if line.strip()]
    assert len(lines) == 1
    message = json.loads(lines[0])
    assert child.returncode == 1 and message["type"] == "error"
    assert message["code"] in {"task_inputs_unavailable", "task_kind_unregistered"}
