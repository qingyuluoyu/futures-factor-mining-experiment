"""Real native CPU QA inside the existing Windows Job; not a production ML worker."""
import json
import os
from pathlib import Path
import sys

import pytest


@pytest.mark.asyncio
async def test_factor_ic_and_real_ranker_complete_inside_hard_memory_process_boundary(tmp_path):
    from app.services.stock_research_v2.windows_job_process import spawn_bounded_process
    from app.services.stock_research_worker import supervise_process
    root = Path(__file__).resolve().parents[1]
    code = "\n".join([
        "import json",
        "from pathlib import Path",
        "from uuid import UUID",
        "from app.schemas.stock_research_v2 import Scope,DatasetRef",
        "from app.services.stock_research_v2.artifacts import ArtifactStore",
        "from app.services.stock_research_v2.factor_values import builtin_factor",
        "from app.services.stock_research_v2.research_experiment import run_experiment",
        "from tests.test_stock_factor_values_v2 import frames,config",
        "from tests.test_stock_ml_dataset import split_spec",
        "data=frames()",
        f"store=ArtifactStore(Path({str(tmp_path / 'artifacts')!r}))",
        "result=run_experiment(data,[builtin_factor('roe'),builtin_factor('pb')],scope=Scope(owner_id=UUID(int=1),trading_mode='virtual'),dataset=DatasetRef(dataset_id=UUID(int=2),data_version='a'*64,qualification='observation_only'),split=split_spec(data),horizon=3,groups=5,top_n=5,config=config(),qa_only=True,store=store,training_parameters={'n_estimators':30,'max_depth':4,'num_leaves':15,'min_child_samples':5})",
        f"Path({str(tmp_path / 'qa-report.json')!r}).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),encoding='utf-8')",
        "payload={key:result[key] for key in ['semantic_sha256','candidate_count','saved_model','saved_report']}",
        "print(json.dumps({'type':'result','result':payload}),flush=True)",
    ])
    environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    child = spawn_bounded_process([sys.executable, "-u", "-c", code], cwd=root, env=environment)

    async def heartbeat():
        return True

    async def reject_snapshot(value):
        raise AssertionError("QA study does not emit database snapshots")

    try:
        result = await supervise_process(child, heartbeat=heartbeat, snapshot=reject_snapshot, timeout_seconds=30, max_message_bytes=4096)
        report = json.loads((tmp_path / "qa-report.json").read_text(encoding="utf-8"))
        assert result["semantic_sha256"] == report["semantic_sha256"] and result["candidate_count"] == 900
        assert report["model"]["runtime"]["lightgbm"] == "4.7.0"
        assert report["model"]["validation_status"] == "qa_only"
        assert report["portfolio_backtest"]["available"] is False
        stats = child.stats()
        assert child.poll() is not None and stats["active_processes"] == 0
        assert stats["memory_limit_bytes"] == 512 * 1024 * 1024 and stats["process_limit"] == 4
        assert 0 < stats["peak_committed_bytes"] <= stats["memory_limit_bytes"]
        print("native_ml_qa_job_stats=" + json.dumps(stats))
    finally:
        child.close()
