[CmdletBinding()]
param(
    [ValidateRange(1, 90)]
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$logDir = Join-Path $projectRoot 'logs'
$python = Join-Path $backendDir '.venv/Scripts/python.exe'
$serviceScript = Join-Path $backendDir 'scripts/local_service.py'
$pgBin = 'C:/Program Files/PostgreSQL/17/bin'
$pgData = 'F:/qihuo-pg-data'
$statePath = Join-Path $logDir 'local-services.json'
$state = @{}
$startupLock = $null

function Get-LocalProcess([int]$ProcessId) {
    return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
}

function Save-Service([string]$Name, [int]$ProcessId) {
    $state[$Name] = @{ pid = $ProcessId; updated_at = [DateTime]::UtcNow.ToString('o') }
    $temporaryPath = "$statePath.$PID.tmp"
    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
    Move-Item -LiteralPath $temporaryPath -Destination $statePath -Force
}

function Start-Hidden([string]$Name, [string]$Executable, [string]$Arguments, [string]$Directory) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
    $inputPath = Join-Path $logDir "$Name-$stamp.stdin"
    $specificationPath = Join-Path $logDir "$Name-$stamp.spawn.json"
    $pidPath = Join-Path $logDir "$Name-$stamp.pid"
    @{
        executable = $Executable
        arguments = $Arguments
        directory = $Directory
        stdout = Join-Path $logDir "$Name-$stamp.out.log"
        stderr = Join-Path $logDir "$Name-$stamp.err.log"
        pid_file = $pidPath
    } | ConvertTo-Json | Set-Content -LiteralPath $specificationPath -Encoding UTF8
    [IO.File]::WriteAllBytes($inputPath, [byte[]]@())
    $bootstrap = Start-Process -FilePath $python -ArgumentList "`"$serviceScript`" spawn `"$specificationPath`"" -WorkingDirectory $Directory -WindowStyle Hidden -PassThru -RedirectStandardInput $inputPath -RedirectStandardOutput (Join-Path $logDir "$Name-$stamp.spawn.out.log") -RedirectStandardError (Join-Path $logDir "$Name-$stamp.spawn.err.log")
    $null = $bootstrap.Handle
    if (-not $bootstrap.WaitForExit(10000)) { throw "$Name process bootstrap timed out; inspect logs/." }
    if ($bootstrap.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $pidPath)) { throw "$Name process bootstrap failed; inspect logs/." }
    $processId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    $process = Get-Process -Id $processId -ErrorAction Stop
    $null = $process.Handle
    Save-Service $Name $process.Id
    return $process
}

function Wait-Ready([string]$Name, [scriptblock]$Check) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (& $Check) { return }
        Start-Sleep -Milliseconds 750
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name readiness timed out; inspect logs under logs/."
}

function Get-Listener([int]$Port) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    return @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
}

function Test-Keepalive($Process) {
    if ($null -eq $Process -or [IO.Path]::GetFileName($Process.ExecutablePath) -ne 'wsl.exe') { return $false }
    $commandLine = ($Process.CommandLine -replace '"', '').Trim()
    return $commandLine -match '(?i)(?:^|\s)-d Ubuntu-24\.04 --exec sleep infinity$'
}

function Assert-ServiceIdentity([string]$Name, [int]$Port) {
    $processIds = @(Get-Listener $Port)
    foreach ($processId in $processIds) {
        $process = Get-LocalProcess $processId
        if ($null -eq $process) { throw "$Name listener disappeared; rerun the launcher." }
        if ($Name -eq 'frontend') {
            $identityOk = [IO.Path]::GetFileName($process.ExecutablePath) -eq 'node.exe' -and $process.CommandLine.Contains($frontendDir) -and $process.CommandLine -match 'next[\\/]dist[\\/](server[\\/]lib[\\/]start-server\.js|bin[\\/]next)'
        } else {
            $parent = Get-LocalProcess $process.ParentProcessId
            $servicePattern = '(?i)(local_service\.py|start-local-service\.py)"?\s+' + [regex]::Escape($Name) + '(?:\s|$)'
            $projectIdentity = $process.CommandLine.Contains($projectRoot) -or ($null -ne $parent -and $parent.CommandLine.Contains($python))
            $identityOk = $projectIdentity -and $process.CommandLine -match $servicePattern
        }
        if (-not $identityOk) { throw "Port $Port belongs to another process; no process was stopped." }
        Save-Service $Name $processId
    }
    return $processIds.Count -gt 0
}

function Get-Health([string]$Url) {
    try {
        return Invoke-RestMethod -Uri $Url -TimeoutSec 3 -ErrorAction Stop
    } catch {
        return $null
    }
}

try {
    foreach ($requiredPath in @($python, $serviceScript, "$pgBin/pg_ctl.exe", "$pgBin/pg_isready.exe", "$pgData/PG_VERSION", "$frontendDir/node_modules/next/dist/bin/next")) {
        if (-not (Test-Path -LiteralPath $requiredPath)) { throw 'A required local runtime or initialized cluster is missing.' }
    }
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    try {
        $startupLock = [IO.File]::Open((Join-Path $logDir 'local-startup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch { throw 'Another local launcher is running; retry after it exits.' }
    if (Test-Path -LiteralPath $statePath) {
        try {
            $storedState = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            foreach ($property in $storedState.PSObject.Properties) { $state[$property.Name] = $property.Value }
        } catch { throw 'Invalid logs/local-services.json; inspect local launcher state before retrying.' }
    }

    Write-Host '[1/5] WSL keepalive and Redis 127.0.0.1:6380'
    $keepalive = $null
    if ($state.ContainsKey('wsl-keepalive')) {
        $candidate = Get-LocalProcess ([int]$state['wsl-keepalive'].pid)
        if (Test-Keepalive $candidate) { $keepalive = $candidate }
    }
    $legacyPidPath = Join-Path $logDir 'wsl-metals-keepalive.pid'
    if ($null -eq $keepalive -and (Test-Path -LiteralPath $legacyPidPath)) {
        $legacyPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $legacyPidPath -Raw).Trim(), [ref]$legacyPid)) {
            $candidate = Get-LocalProcess $legacyPid
            if (Test-Keepalive $candidate) { $keepalive = $candidate }
        }
    }
    if ($null -eq $keepalive) {
        $keepalive = Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" | Where-Object { Test-Keepalive $_ } | Select-Object -First 1
    }
    if ($null -eq $keepalive) {
        $keepalive = Start-Hidden 'wsl-keepalive' 'wsl.exe' '-d Ubuntu-24.04 --exec sleep infinity' $projectRoot
    }
    if ($keepalive -is [System.Diagnostics.Process]) {
        Save-Service 'wsl-keepalive' $keepalive.Id
    } else {
        Save-Service 'wsl-keepalive' $keepalive.ProcessId
    }
    Wait-Ready 'Redis' {
        try {
            & $python $serviceScript redis-check 2>$null | Out-Null
            return $LASTEXITCODE -eq 0
        } catch { return $false }
    }

    Write-Host '[2/5] PostgreSQL dedicated cluster 127.0.0.1:5433'
    $pgRunning = $false
    try {
        & "$pgBin/pg_ctl.exe" -D $pgData status 2>$null | Out-Null
        $pgRunning = $LASTEXITCODE -eq 0
    } catch { $pgRunning = $false }
    if (-not $pgRunning) {
        if (@(Get-Listener 5433).Count -gt 0) { throw 'Port 5433 is occupied by another cluster; no process was stopped.' }
        $pgStart = Start-Hidden 'postgres-startup' "$pgBin/pg_ctl.exe" "-D `"$pgData`" -l `"$logDir/postgresql.log`" -w -t $TimeoutSeconds start" $projectRoot
        if (-not $pgStart.WaitForExit(($TimeoutSeconds + 5) * 1000)) { throw 'PostgreSQL startup timed out; inspect logs/.' }
        if ($pgStart.ExitCode -ne 0) { throw 'PostgreSQL startup failed; inspect logs/.' }
    }
    Wait-Ready 'PostgreSQL' {
        try {
            & "$pgBin/pg_isready.exe" -h 127.0.0.1 -p 5433 -t 2 2>$null | Out-Null
            return $LASTEXITCODE -eq 0
        } catch { return $false }
    }
    $pgListeners = @(Get-Listener 5433)
    foreach ($pgProcessId in $pgListeners) {
        $pgProcess = Get-LocalProcess $pgProcessId
        if ($null -eq $pgProcess -or -not $pgProcess.CommandLine.Replace('\', '/').Contains($pgData)) { throw 'Port 5433 does not belong to the dedicated PostgreSQL cluster.' }
        Save-Service 'postgresql' $pgProcessId
    }

    Write-Host '[3/5] Backend http://127.0.0.1:8002'
    if (-not (Assert-ServiceIdentity 'backend' 8002)) {
        Start-Hidden 'backend' $python "`"$serviceScript`" backend" $backendDir | Out-Null
    }
    Wait-Ready 'Backend' {
        $health = Get-Health 'http://127.0.0.1:8002/health'
        return $null -ne $health -and $health.status -eq 'ok' -and $null -ne $health.market -and -not $health.market.error
    }
    if (-not (Assert-ServiceIdentity 'backend' 8002)) { throw 'Backend listener disappeared after readiness.' }
    $market = (Get-Health 'http://127.0.0.1:8002/health').market
    Write-Host "    market.ok=$($market.ok); live.ok=$($market.live.ok); virtual.ok=$($market.virtual.ok)"
    if (-not $market.ok) { Write-Host '    Market data is incomplete; service readiness does not verify live trading data.' }

    Write-Host '[4/5] Sina bridge http://127.0.0.1:8110'
    if (-not (Assert-ServiceIdentity 'bridge' 8110)) {
        Start-Hidden 'bridge' $python "`"$serviceScript`" bridge" $backendDir | Out-Null
    }
    Wait-Ready 'Sina bridge' {
        $health = Get-Health 'http://127.0.0.1:8110/health'
        return $null -ne $health -and $health.ok -eq $true -and $health.redis -eq $true
    }
    if (-not (Assert-ServiceIdentity 'bridge' 8110)) { throw 'Sina bridge listener disappeared after readiness.' }

    Write-Host '[5/5] Frontend http://127.0.0.1:3000'
    if (-not (Assert-ServiceIdentity 'frontend' 3000)) {
        $node = (Get-Command node.exe -ErrorAction Stop).Source
        $previousBackendUrl = $env:BACKEND_URL
        try {
            $env:BACKEND_URL = 'http://127.0.0.1:8002'
            Start-Hidden 'frontend' $node "`"$frontendDir/node_modules/next/dist/bin/next`" dev --hostname 127.0.0.1 --port 3000" $frontendDir | Out-Null
        } finally { $env:BACKEND_URL = $previousBackendUrl }
    }
    Wait-Ready 'Frontend' {
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3000' -UseBasicParsing -TimeoutSec 3
            return $response.StatusCode -eq 200
        } catch { return $false }
    }
    if (-not (Assert-ServiceIdentity 'frontend' 3000)) { throw 'Frontend listener disappeared after readiness.' }
    Write-Host 'Local services ready: http://127.0.0.1:3000 (market data status shown separately).'
} catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($null -ne $startupLock) { $startupLock.Dispose() }
}
