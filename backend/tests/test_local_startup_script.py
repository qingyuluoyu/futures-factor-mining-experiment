import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PROJECT_ROOT / "scripts" / "run-local.ps1"
SERVICE = PROJECT_ROOT / "backend" / "scripts" / "local_service.py"


def load_service():
    assert SERVICE.is_file(), "The versioned local service entrypoint is missing"
    spec = importlib.util.spec_from_file_location("local_service", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_environment_overrides_base_file(tmp_path, monkeypatch):
    service = load_service()
    (tmp_path / "backend").mkdir()
    (tmp_path / ".env").write_text("LOCAL_STARTUP_TEST_VALUE=base\n", encoding="utf-8")
    (tmp_path / ".env.runtime").write_text(
        "LOCAL_STARTUP_TEST_VALUE=runtime\n", encoding="utf-8"
    )
    monkeypatch.setenv("LOCAL_STARTUP_TEST_VALUE", "inherited")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    service.configure_environment(tmp_path)
    assert os.environ["LOCAL_STARTUP_TEST_VALUE"] == "runtime"
    assert Path.cwd() == tmp_path / "backend"
    assert sys.path[0] == str(tmp_path / "backend")


def test_unknown_service_rejected_without_starting_services():
    assert SERVICE.is_file(), "The versioned local service entrypoint is missing"
    result = subprocess.run(
        [sys.executable, str(SERVICE), "unknown"], capture_output=True, timeout=10
    )
    assert result.returncode == 2
    assert b"invalid choice" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
def test_powershell_syntax_and_hidden_background_processes():
    assert LAUNCHER.is_file(), "The Windows launcher is missing"
    command = """
    param($Launcher)
    $tokens = $null
    $parseErrors = $null
    $syntax = [System.Management.Automation.Language.Parser]::ParseFile(
        $Launcher, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { exit 1 }
    $starts = $syntax.FindAll({ param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq 'Start-Process'
    }, $true)
    if ($starts.Count -lt 1) { exit 2 }
    foreach ($start in $starts) {
        if ($start.Extent.Text -notmatch '-WindowStyle Hidden') { exit 3 }
        if ($start.Extent.Text -notmatch '-RedirectStandardInput') { exit 4 }
    }
    """
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", f"& {{ {command} }} '{LAUNCHER}'"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_redis_probe_disables_implicit_retries_and_resp3(monkeypatch):
    import redis

    service = load_service()
    observed = {}
    original_client = redis.Redis

    def capture_client(**options):
        observed.update(options)
        client = original_client(**options)
        monkeypatch.setattr(client, "ping", lambda: True)
        return client

    monkeypatch.setattr(service, "configure_environment", lambda root: None)
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_DB", "0")
    monkeypatch.setattr(redis, "Redis", capture_client)
    monkeypatch.setattr(sys, "argv", [str(SERVICE), "redis-check"])
    assert service.main() == 0
    assert observed.get("protocol") == 2
    retry = observed["retry"]
    attempts = []

    def unavailable():
        attempts.append(1)
        raise redis.ConnectionError("unavailable")

    with pytest.raises(redis.ConnectionError):
        retry.call_with_retry(unavailable, lambda failure: None)
    assert len(attempts) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
@pytest.mark.parametrize("shell_name", ("pwsh.exe", "powershell.exe"))
def test_background_child_does_not_hold_launcher_open(tmp_path, shell_name):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is not installed")
    script = tmp_path / "start-harmless-child.ps1"
    script.write_text(
        """
param($Launcher, $Python, $TestDirectory)
$ErrorActionPreference = 'Stop'
$logDir = $TestDirectory
$python = $Python
$serviceScript = Join-Path (Split-Path -Parent (Split-Path -Parent $Launcher)) 'backend/scripts/local_service.py'
$statePath = Join-Path $TestDirectory 'local-services.json'
$state = @{}
$tokens = $null
$parseErrors = $null
$syntax = [System.Management.Automation.Language.Parser]::ParseFile(
    $Launcher, [ref]$tokens, [ref]$parseErrors)
foreach ($name in @('Save-Service', 'Start-Hidden')) {
    $definition = $syntax.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $name
    }, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
$child = Start-Hidden 'harmless-child' $Python '-c "import os,time; from pathlib import Path; Path(''actual-child.pid'').write_text(str(os.getpid())); time.sleep(20)"' $TestDirectory
$child.Id | Set-Content (Join-Path $TestDirectory 'child.pid')
Write-Output 'inner-returned'
""",
        encoding="utf-8-sig",
    )
    command = "& '{shell}' -NoProfile -File '{script}' '{launcher}' '{python}' '{directory}'; Write-Output 'outer-returned'".format(
        shell=shell.replace("'", "''"),
        script=str(script).replace("'", "''"),
        launcher=str(LAUNCHER).replace("'", "''"),
        python=sys.executable.replace("'", "''"),
        directory=str(tmp_path).replace("'", "''"),
    )
    started = time.monotonic()
    caller = subprocess.Popen(
        [shell, "-NoProfile", "-Command", command],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        output, errors = caller.communicate(timeout=8)
        assert caller.returncode == 0, errors.decode(errors="replace")
        assert b"inner-returned" in output, errors.decode(errors="replace")
        assert b"outer-returned" in output
        assert time.monotonic() - started < 8
        state = json.loads((tmp_path / "local-services.json").read_text(encoding="utf-8-sig"))
        child_pid = state["harmless-child"]["pid"]
        assert subprocess.run(
            [shell, "-NoProfile", "-Command", f"if (-not (Get-Process -Id {child_pid} -ErrorAction SilentlyContinue)) {{ exit 1 }}"],
            capture_output=True, timeout=5,
        ).returncode == 0
    finally:
        if caller.poll() is None:
            caller.kill()
        for pid_name in ("actual-child.pid", "child.pid"):
            child_pid_path = tmp_path / pid_name
            if child_pid_path.exists():
                child_pid = int(child_pid_path.read_text(encoding="utf-8-sig").strip())
                subprocess.run(
                    [shell, "-NoProfile", "-Command", f"Stop-Process -Id {child_pid} -ErrorAction SilentlyContinue"],
                    capture_output=True, timeout=5,
                )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
def test_zero_startup_timeout_rejected_before_startup():
    assert LAUNCHER.is_file(), "The Windows launcher is missing"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(LAUNCHER), "-TimeoutSeconds", "0"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
def test_keepalive_identity_rejects_recycled_pid_from_other_process():
    assert LAUNCHER.is_file(), "The Windows launcher is missing"
    command = """
    param($Launcher)
    $tokens = $null
    $parseErrors = $null
    $syntax = [System.Management.Automation.Language.Parser]::ParseFile(
        $Launcher, [ref]$tokens, [ref]$parseErrors)
    $definition = $syntax.Find({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-Keepalive'
    }, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
    $candidate = [pscustomobject]@{
        ExecutablePath = 'C:/Windows/System32/wsl.exe'
        CommandLine = 'wsl.exe -d Ubuntu-24.04 --exec sleep infinity'
    }
    if (-not (Test-Keepalive $candidate)) { exit 1 }
    $candidate.CommandLine = 'wsl.exe -d AnotherDistro --exec sleep infinity'
    if (Test-Keepalive $candidate) { exit 2 }
    $candidate.CommandLine = 'wsl.exe -d Ubuntu-24.04 --exec sleep infinity'
    $candidate.ExecutablePath = 'C:/other.exe'
    if (Test-Keepalive $candidate) { exit 3 }
    if (Test-Keepalive $null) { exit 4 }
    """
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", f"& {{ {command} }} '{LAUNCHER}'"],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
