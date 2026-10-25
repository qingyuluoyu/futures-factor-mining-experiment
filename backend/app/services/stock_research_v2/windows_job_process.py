"""Stock-only Windows 10+ process boundary, assigned atomically at creation.

Uses JOB_LIST rather than the create-suspended/assign/resume race. The job is
unnamed, non-inheritable and kill-on-close; no breakaway flags are permitted.
This bounds committed memory, not the process working set or disk usage.
"""
import ctypes as C
from ctypes import wintypes as W
import math
import os
from pathlib import Path
import subprocess
import time

from app.services.stock_research_jobs import JobError

MIB = 1024 * 1024


class BasicLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", W.DWORD),
        ("min_working_set", C.c_size_t), ("max_working_set", C.c_size_t), ("processes", W.DWORD),
        ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic", BasicLimits), ("io", C.c_uint64 * 6), ("process_memory", C.c_size_t),
        ("job_memory", C.c_size_t), ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]


class Accounting(C.Structure):
    _fields_ = [("times", C.c_int64 * 4), ("faults", W.DWORD), ("total", W.DWORD),
        ("active", W.DWORD), ("terminated", W.DWORD)]


class Startup(C.Structure):
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR), ("title", W.LPWSTR),
        ("x", W.DWORD), ("y", W.DWORD), ("width", W.DWORD), ("height", W.DWORD),
        ("chars_x", W.DWORD), ("chars_y", W.DWORD), ("fill", W.DWORD), ("flags", W.DWORD),
        ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_bytes", C.c_void_p),
        ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class StartupEx(C.Structure):
    _fields_ = [("startup", Startup), ("attributes", C.c_void_p)]


class ProcessInfo(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


def kernel_api():
    k = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([C.c_void_p, W.LPCWSTR], W.HANDLE),
        "SetInformationJobObject": ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
        "QueryInformationJobObject": ([W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.c_void_p], W.BOOL),
        "InitializeProcThreadAttributeList": ([C.c_void_p, W.DWORD, W.DWORD, C.POINTER(C.c_size_t)], W.BOOL),
        "UpdateProcThreadAttribute": ([C.c_void_p, W.DWORD, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p], W.BOOL),
        "DeleteProcThreadAttributeList": ([C.c_void_p], None),
        "CreateProcessW": ([W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
            C.c_void_p, W.LPCWSTR, C.POINTER(StartupEx), C.POINTER(ProcessInfo)], W.BOOL),
        "CloseHandle": ([W.HANDLE], W.BOOL),
        "TerminateJobObject": ([W.HANDLE, W.UINT], W.BOOL),
        "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
        "GetExitCodeProcess": ([W.HANDLE, C.POINTER(W.DWORD)], W.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(k, name)
        function.argtypes, function.restype = args, result
    return k


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


class BoundedProcess:
    def __init__(self, kernel, job, info, stdin, stdout):
        self._kernel, self._job, self._process = kernel, job, info.process
        self.pid, self.returncode = info.pid, None
        self.stdin, self.stdout = stdin, stdout

    def stats(self):
        limits, accounting = ExtendedLimits(), Accounting()
        checked(self._kernel.QueryInformationJobObject(self._job, 9, C.byref(limits), C.sizeof(limits), None))
        checked(self._kernel.QueryInformationJobObject(self._job, 1, C.byref(accounting), C.sizeof(accounting), None))
        return {"memory_limit_bytes": limits.job_memory, "peak_committed_bytes": limits.peak_job,
            "process_limit": limits.basic.processes, "active_processes": accounting.active}

    def terminate(self):
        if self._job:
            checked(self._kernel.TerminateJobObject(self._job, 1))

    kill = terminate

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        status = self._kernel.WaitForSingleObject(self._process, 0)
        if status == 258:
            return None
        if status != 0:
            raise OSError("Process wait failed")
        result = W.DWORD()
        checked(self._kernel.GetExitCodeProcess(self._process, C.byref(result)))
        self.returncode = result.value
        # A root that exited cannot leave a descendant holding IPC pipes open.
        self.terminate()
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("invalid wait timeout")
        milliseconds = 0xFFFFFFFF if timeout is None else min(0xFFFFFFFE, math.ceil(timeout * 1000))
        status = self._kernel.WaitForSingleObject(self._process, milliseconds)
        if status == 258:
            raise subprocess.TimeoutExpired("stock-observation-process", timeout)
        if status != 0:
            raise OSError("Process wait failed")
        return self.poll()

    def close(self):
        if not self._job:
            return
        try:
            self.terminate()
            self.wait(timeout=3)
            deadline = time.monotonic() + 3
            while self.stats()["active_processes"]:
                if time.monotonic() >= deadline:
                    raise OSError("Owned process tree did not exit within cleanup budget")
                time.sleep(0.01)
        finally:
            try:
                checked(self._kernel.CloseHandle(self._job))
                self._job = None
                # kill-on-close is the fallback if explicit termination failed.
                self.wait(timeout=3)
            finally:
                if self._process:
                    checked(self._kernel.CloseHandle(self._process))
                    self._process = None
                for stream in (self.stdin, self.stdout):
                    if stream is not None and not stream.closed:
                        stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def spawn_bounded_process(argv, *, cwd, env, memory_limit_bytes=512 * MIB, process_limit=4):
    if type(memory_limit_bytes) is not int or not 128 * MIB <= memory_limit_bytes <= 2048 * MIB or type(process_limit) is not int or not 1 <= process_limit <= 4:
        raise JobError("invalid_execution_budget", "观察任务资源预算无效", 422)
    if os.name != "nt":
        raise JobError("worker_resource_unavailable", "当前系统尚未实现观察执行器硬资源边界", 503)
    if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not Path(argv[0]).is_absolute():
        raise JobError("invalid_execution_budget", "观察执行程序参数无效", 422)
    if any(not isinstance(k, str) or not isinstance(v, str) or "=" in k or "\0" in k or "\0" in v for k, v in env.items()):
        raise JobError("invalid_execution_budget", "观察执行环境无效", 422)
    import msvcrt
    kernel, job, attributes, initialized = kernel_api(), None, None, False
    fds, info = [], ProcessInfo()
    try:
        job = checked(kernel.CreateJobObjectW(None, None))
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000 | 0x200 | 0x100 | 0x8  # kill-on-close, job/process commit, active processes
        limits.basic.processes = process_limit
        limits.job_memory = limits.process_memory = memory_limit_bytes
        checked(kernel.SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
        size = C.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 2, 0, C.byref(size))
        if not size.value:
            raise OSError("Attribute sizing failed")
        attributes = C.create_string_buffer(size.value)
        checked(kernel.InitializeProcThreadAttributeList(attributes, 2, 0, C.byref(size)))
        initialized = True
        child_in, parent_in = os.pipe()
        fds.extend([child_in, parent_in])
        parent_out, child_out = os.pipe()
        fds.extend([parent_out, child_out])
        child_err = os.open(os.devnull, os.O_WRONLY)
        fds.append(child_err)
        for fd in (child_in, child_out, child_err):
            os.set_inheritable(fd, True)
        inherited = (W.HANDLE * 3)(*[msvcrt.get_osfhandle(fd) for fd in (child_in, child_out, child_err)])
        jobs = (W.HANDLE * 1)(job)
        checked(kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002, inherited, C.sizeof(inherited), None, None))
        checked(kernel.UpdateProcThreadAttribute(attributes, 0, 0x2000D, jobs, C.sizeof(jobs), None, None))
        startup = StartupEx()
        startup.startup.cb, startup.startup.flags = C.sizeof(startup), 0x100
        startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = inherited
        startup.attributes = C.cast(attributes, C.c_void_p)
        block = C.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda pair: pair[0].upper())) + "\0\0")
        command = C.create_unicode_buffer(subprocess.list2cmdline(argv))
        checked(kernel.CreateProcessW(argv[0], command, None, None, True, 0x80000 | 0x400 | 0x8000000,
            block, str(Path(cwd).absolute()), C.byref(startup), C.byref(info)))
        checked(kernel.CloseHandle(info.thread))
        info.thread = None
        for fd in (child_in, child_out, child_err):
            os.close(fd)
            fds.remove(fd)
        stdin = os.fdopen(parent_in, "wb")
        fds.remove(parent_in)
        try:
            stdout = os.fdopen(parent_out, "rb")
            fds.remove(parent_out)
        except BaseException:
            stdin.close()
            raise
        return BoundedProcess(kernel, job, info, stdin, stdout)
    except BaseException:
        if job:
            kernel.CloseHandle(job)
        if info.process:
            kernel.WaitForSingleObject(info.process, 3000)
            kernel.CloseHandle(info.process)
        if info.thread:
            kernel.CloseHandle(info.thread)
        raise
    finally:
        if initialized:
            kernel.DeleteProcThreadAttributeList(attributes)
        for fd in fds:
            os.close(fd)
