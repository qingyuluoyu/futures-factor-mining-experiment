import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_dataset_process_supervisor import alive, process


def receipt(qualification="observation_only"):
    return {"columnar_id": str(uuid4()), "dataset": {"dataset_id": str(uuid4()), "data_version": "a" * 64,
        "qualification": qualification}, "manifest_sha256": "b" * 64}


@pytest.mark.asyncio
async def test_real_parquet_and_columnar_protocol_run_inside_existing_windows_memory_job(tmp_path):
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    from app.services.stock_research_v2.windows_job_process import spawn_bounded_process
    payload = receipt()
    code = "\n".join([
        "import json", "from pathlib import Path", "from uuid import uuid4",
        "from tests.test_stock_columnar_store import observations",
        "from app.services.stock_research_v2.columnar_store import ParquetStore",
        f"store=ParquetStore(Path({str(tmp_path)!r}))",
        "store.put_page(observations(),source_id=uuid4(),source_sha256='a'*64,data_version='a'*64)",
        f"payload={payload!r}",
        f"print(json.dumps({{'type':'result','result':payload}}),flush=True)",
    ])
    diagnostic = tmp_path / "qa-child-diagnostic.txt"
    steps = []
    for index, line in enumerate(code.splitlines()):
        steps.extend([f"diag.write_text('step {index}',encoding='utf-8')", line])
    code = f"from pathlib import Path\ndiag=Path({str(diagnostic)!r})\ntry:\n" + "\n".join("    " + line for line in steps) + "\nexcept BaseException as error:\n    diag.write_text(type(error).__name__,encoding='utf-8')\n    raise"
    environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    child = spawn_bounded_process([sys.executable, "-u", "-c", code], cwd=Path(__file__).resolve().parents[1], env=environment)
    try:
        try:
            parsed = await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=20, columnar=True)
        except JobError:
            print("columnar_qa_child_exit=" + str(child.poll()))
            print("columnar_qa_diagnostic=" + (diagnostic.read_text(encoding="utf-8") if diagnostic.exists() else "not entered"))
            print("columnar_qa_failed_stats=" + json.dumps(child.stats()))
            raise
        assert parsed.model_dump(mode="json") == payload
        assert child.poll() is not None and child.stdout.closed
        stats = child.stats()
        assert stats["memory_limit_bytes"] == 512 * 1024 * 1024 and stats["process_limit"] == 4
        assert 0 < stats["peak_committed_bytes"] <= stats["memory_limit_bytes"]
        assert all(path.read_bytes().startswith(b"PAR1") for path in tmp_path.glob("*.parquet"))
        assert len(list(tmp_path.glob("*.parquet"))) == 1
        print("columnar_qa_job_stats=" + json.dumps(stats))
    finally:
        child.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [receipt("backtest_ready"), {"dataset_id": str(uuid4()), "data_version": "a" * 64, "qualification": "observation_only"}, {**receipt(), "owner_id": str(uuid4())}])
async def test_columnar_mode_rejects_wrong_result_kind_qualification_and_extra_control_fields(payload):
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    child = process(f"import json; print(json.dumps({{'type':'result','result':{payload!r}}}),flush=True)")
    with pytest.raises(JobError) as error:
        await supervise_dataset_process(child, heartbeat=alive, timeout_seconds=5, columnar=True)
    assert error.value.code == "dataset_invalid" and child.poll() is not None
