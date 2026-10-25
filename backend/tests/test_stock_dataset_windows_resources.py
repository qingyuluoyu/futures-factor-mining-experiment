import asyncio
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024


def spawn(code, **limits):
    from app.services.stock_research_v2.windows_job_process import spawn_bounded_process
    return spawn_bounded_process([sys.executable, "-u", "-c", code], cwd=ROOT, env=os.environ.copy(), **limits)


def test_process_starts_inside_job_and_kernel_enforces_memory_commit_limit():
    code = """
import ctypes
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.IsProcessInJob.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)]
k.GetCurrentProcess.restype=wintypes.HANDLE
flag=wintypes.BOOL()
assert k.IsProcessInJob(k.GetCurrentProcess(),None,ctypes.byref(flag)) and flag.value
try:
    value=bytearray(256*1024*1024)
except MemoryError:
    print('bounded',flush=True)
else:
    print('unbounded',flush=True)
"""
    with spawn(code, memory_limit_bytes=128 * MIB) as process:
        assert process.wait(timeout=10) == 0
        assert process.stdout.read(256).strip() == b"bounded"
        stats = process.stats()
        assert stats["memory_limit_bytes"] == 128 * MIB
        assert 0 < stats["peak_committed_bytes"] <= 128 * MIB
        assert stats["process_limit"] == 4


@pytest.mark.parametrize("limit", [True, 0, 127 * MIB, 2049 * MIB])
def test_invalid_resource_budget_rejected_before_process_creation(limit):
    from app.services.stock_research_jobs import JobError
    with pytest.raises(JobError) as error:
        spawn("raise AssertionError('must not launch')", memory_limit_bytes=limit)
    assert error.value.code == "invalid_execution_budget"


def open_process(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    assert handle
    return kernel, handle


@pytest.mark.asyncio
async def test_close_kills_descendant_even_when_it_does_not_share_parent_pipes():
    code = "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(child.pid,flush=True); sys.stdin.readline()"
    process = spawn(code)
    handle = None
    try:
        pid = int(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline, 64), timeout=5))
        kernel, handle = open_process(pid)
        # The venv redirector can introduce an additional native Python process.
        assert 2 <= process.stats()["active_processes"] <= 4
        await asyncio.to_thread(process.close)
        assert kernel.WaitForSingleObject(handle, 5000) == 0
        assert process.poll() is not None
        process.close()
    finally:
        process.close()
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.asyncio
async def test_existing_supervision_cancellation_reaps_bounded_tree():
    from app.services.stock_research_v2.process_supervisor import supervise_dataset_process
    process = spawn("import time; time.sleep(30)")
    async def lost():
        return False
    try:
        assert await supervise_dataset_process(process, heartbeat=lost, timeout_seconds=5) is None
        assert process.poll() is not None
    finally:
        process.close()


@pytest.mark.asyncio
async def test_parent_abrupt_exit_closes_noninherited_job_and_kills_child():
    code = """
import os,sys
from pathlib import Path
from app.services.stock_research_v2.windows_job_process import spawn_bounded_process
p=spawn_bounded_process([sys.executable,'-c','import time; time.sleep(30)'],cwd=Path.cwd(),env=os.environ.copy())
print(p.pid,flush=True)
sys.stdin.readline()
os._exit(77)
"""
    parent = subprocess.Popen([sys.executable, "-u", "-c", code], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    handle = None
    try:
        pid = int(await asyncio.wait_for(asyncio.to_thread(parent.stdout.readline, 64), timeout=10))
        kernel, handle = open_process(pid)
        parent.stdin.write(b"exit\n")
        parent.stdin.flush()
        assert await asyncio.to_thread(parent.wait, timeout=5) == 77
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if parent.poll() is None:
            parent.stdin.write(b"exit\n")
            parent.stdin.flush()
            await asyncio.to_thread(parent.wait, timeout=5)
        parent.stdin.close()
        parent.stdout.close()
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.asyncio
async def test_stock_launcher_uses_resource_boundary_and_closes_job_on_failure(tmp_path, monkeypatch):
    from app.services.stock_research_v2 import dataset_worker
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.services.stock_research_jobs import JobError
    from tests.test_stock_dataset_claim_execution import claimed
    from tests.test_stock_research_job_service import job_database
    from tests.test_stock_research_v2_jobs import setup
    original = dataset_worker.spawn_bounded_process
    seen = []
    def recording(*args, **kwargs):
        process = original(*args, **kwargs)
        seen.append(process)
        return process
    monkeypatch.setattr(dataset_worker, "spawn_bounded_process", recording)
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "data")
        claim = await claimed(factory, scope, command)
        store = ArtifactStore(tmp_path / "gone")
        store.root.rmdir()
        async def alive():
            return True
        with pytest.raises(JobError):
            await dataset_worker.run_claim_process(claim, store, alive)
        assert len(seen) == 1
        assert seen[0].poll() is not None and seen[0]._job is None


def test_resource_setup_failure_does_not_fall_back_to_unbounded_process(monkeypatch):
    from app.services.stock_research_v2 import windows_job_process
    original = windows_job_process.kernel_api
    class Denied:
        def __init__(self):
            self.kernel = original()
            self.closed = 0
        def __getattr__(self, name):
            return getattr(self.kernel, name)
        def UpdateProcThreadAttribute(self, *args):
            raise OSError("qa-attribute-denied")
        def CloseHandle(self, handle):
            self.closed += 1
            return self.kernel.CloseHandle(handle)
        def CreateProcessW(self, *args):
            pytest.fail("resource failure must not start any process")
    kernel = Denied()
    monkeypatch.setattr(windows_job_process, "kernel_api", lambda: kernel)
    with pytest.raises(OSError, match="qa-attribute-denied"):
        spawn("print('must not run')")
    assert kernel.closed == 1


def test_native_termination_failure_still_closes_job_handle_and_reaps_owned_process():
    process = spawn("import time; time.sleep(30)")
    original = process._kernel
    kernel, handle = open_process(process.pid)
    class TerminationDenied:
        def __getattr__(self, name):
            return getattr(original, name)
        def TerminateJobObject(self, *args):
            raise OSError("qa-terminate-denied")
    process._kernel = TerminationDenied()
    try:
        with pytest.raises(OSError, match="qa-terminate-denied"):
            process.close()
        assert process._job is None
        assert kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        process._kernel = original
        process.close()
        kernel.CloseHandle(handle)


@pytest.mark.asyncio
async def test_launcher_closes_native_job_even_when_supervisor_stop_raises(tmp_path, monkeypatch):
    from app.services.stock_research_v2 import dataset_worker
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from tests.test_stock_dataset_claim_execution import claimed
    from tests.test_stock_research_job_service import job_database
    from tests.test_stock_research_v2_jobs import setup
    original = dataset_worker.spawn_bounded_process
    seen = []
    def denied(*args, **kwargs):
        process = original(*args, **kwargs)
        kernel = process._kernel
        class TerminationDenied:
            def __getattr__(self, name):
                return getattr(kernel, name)
            def TerminateJobObject(self, *args):
                raise OSError("qa-stop-denied")
        process._kernel = TerminationDenied()
        seen.append((process, kernel))
        return process
    monkeypatch.setattr(dataset_worker, "spawn_bounded_process", denied)
    async with job_database() as (factory, owner, other):
        scope, command = await setup(factory, owner, tmp_path / "data")
        claim = await claimed(factory, scope, command)
        async def lost():
            return False
        try:
            with pytest.raises((OSError, ValueError)):
                await dataset_worker.run_claim_process(claim, ArtifactStore(tmp_path / "data"), lost)
            assert len(seen) == 1 and seen[0][0]._job is None
        finally:
            for process, kernel in seen:
                process._kernel = kernel
                process.close()
