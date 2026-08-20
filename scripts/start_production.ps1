param(
    [ValidateRange(0, 300)][int]$StartupDelaySeconds = 0,
    [ValidateRange(5, 300)][int]$HealthTimeoutSeconds = 60,
    [ValidateRange(30, 600)][int]$QwenTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "launcher_lib.ps1")

$logs = Join-Path $root "logs"
$runtimePath = Join-Path $root "state\production-runtime.json"
$controlPath = Join-Path $root "state\processing-control.json"
$launcherLog = Join-Path $logs "production-launcher.log"
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$mutex = Enter-LauncherMutex "Local\CONTENTOPS_PRODUCTION_LAUNCHER"
if (-not $mutex) {
    Write-Host "Content Ops production is already running."
    exit 2
}

$ytdownload = $null
$qwen_worker = $null
$silence = $null
$watcher = $null
$qwenHealth = $null
$exitCode = 0
$launcherStartedAt = Get-ProcessStartUtc (Get-Process -Id $PID)
$startedAt = (Get-Date).ToUniversalTime().ToString("o")

function Save-ProductionRuntime {
    $state = [ordered]@{
        launcher_pid = $PID
        launcher_started_at = $script:launcherStartedAt
        started_at = $script:startedAt
    }
    foreach ($name in @("ytdownload", "qwen_worker", "silence", "watcher")) {
        $process = Get-Variable -Name $name -ValueOnly
        $state["${name}_pid"] = $(if ($process) { $process.Id } else { $null })
        $state["${name}_started_at"] = $(if ($process) { Get-ProcessStartUtc $process } else { $null })
    }
    $state["qwen_worker_health"] = $script:qwenHealth
    $state["qwen_worker_port"] = 8792
    Write-RuntimeState $runtimePath $state
}

function Save-QwenControl([bool]$Enabled, [string]$Status, [Diagnostics.Process]$Process) {
    $existing = Read-RuntimeState $controlPath
    $state = [ordered]@{}
    if ($existing) {
        foreach ($property in $existing.PSObject.Properties) { $state[$property.Name] = $property.Value }
    }
    $state["silence_engine_enabled"] = $Enabled
    $state["qwen_status"] = $Status
    $state["qwen_pid"] = $(if ($Process) { $Process.Id } else { $null })
    $state["qwen_started_at"] = $(if ($Process) { Get-ProcessStartUtc $Process } else { $null })
    $state["error"] = $null
    $state["off_requested_at"] = $null
    Write-RuntimeState $controlPath $state
}

function Stop-ProductionChildren {
    if ($script:watcher) {
        Stop-OwnedProcessTree $script:watcher.Id (Get-ProcessStartUtc $script:watcher) "uvicorn app.main:app" | Out-Null
    }
    if ($script:silence) {
        Stop-OwnedProcessTree $script:silence.Id (Get-ProcessStartUtc $script:silence) "contentops_process_bridge.py" | Out-Null
    }
    $control = Read-RuntimeState $controlPath
    if ($control -and $control.qwen_pid -and $control.qwen_started_at) {
        Stop-OwnedProcessTree ([int]$control.qwen_pid) ([string]$control.qwen_started_at) "qwen_worker.supervisor" | Out-Null
    } elseif ($script:qwen_worker) {
        Stop-OwnedProcessTree $script:qwen_worker.Id (Get-ProcessStartUtc $script:qwen_worker) "qwen_worker.supervisor" | Out-Null
    }
    if ($script:ytdownload) {
        Stop-OwnedProcessTree $script:ytdownload.Id (Get-ProcessStartUtc $script:ytdownload) "electron.exe" | Out-Null
    }
}

function Assert-Healthy([Diagnostics.Process]$Process, [string]$Name, [string]$Url) {
    $result = Wait-ServiceHealth $Process.Id $Url $HealthTimeoutSeconds
    if ($result -ne "OK") { throw "$Name health failed: $result" }
    Write-LauncherLog $launcherLog "$Name health PASS"
}

function Assert-SilenceIdentity([string]$Url, [string]$ExpectedRoot, [string]$ExpectedPython) {
    $health = Invoke-RestMethod $Url -TimeoutSec 3
    if ($health.status -ne "READY" -or
        [IO.Path]::GetFullPath([string]$health.project_root) -ne [IO.Path]::GetFullPath($ExpectedRoot) -or
        [IO.Path]::GetFullPath([string]$health.python_executable) -ne [IO.Path]::GetFullPath($ExpectedPython)) {
        throw "SILENCE CUTTER identity mismatch"
    }
    Write-LauncherLog $launcherLog "Silence identity PASS pid=$($health.bridge_pid) build=$($health.bridge_build)"
}

try {
    Import-DotEnv (Join-Path $root ".env")
    $control = Read-RuntimeState $controlPath
    $engineEnabled = $(if ($null -eq $control) { $true } else { [bool]$control.silence_engine_enabled })
    if ($StartupDelaySeconds) {
        Write-LauncherLog $launcherLog "startup delay seconds=$StartupDelaySeconds"
        Start-Sleep -Seconds $StartupDelaySeconds
    }

    $driveRoot = [IO.Path]::GetPathRoot($root)
    $ytdownloadRoot = if ($env:YTDOWNLOAD_ROOT) { $env:YTDOWNLOAD_ROOT } else { Join-Path $driveRoot "YTDOWNLOAD" }
    $silenceRoot = if ($env:SILENCE_CUTTER_ROOT) { $env:SILENCE_CUTTER_ROOT } else { Join-Path $driveRoot "Silence_cutter" }
    $electron = Join-Path $ytdownloadRoot "node_modules\electron\dist\electron.exe"
    $silencePython = Join-Path $silenceRoot ".venv_asr_test\Scripts\python.exe"
    $env:SILENCE_PYTHON = $silencePython
    $env:SILENCE_QWEN_ENDPOINT = "http://127.0.0.1:8792"
    $env:CONTENTOPS_ENFORCE_RUNTIME = "1"
    $watcherPython = Get-VenvPython $root
    foreach ($required in @($electron, $silencePython)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Runtime missing: $required" }
    }
    if ($engineEnabled) {
        if (-not $env:SEMANTIC_QWEN_MODEL) {
            $env:SEMANTIC_QWEN_MODEL = Join-Path $silenceRoot "local_models\Qwen2.5-VL-7B-Instruct-AWQ"
        }
        if (-not (Test-Path -LiteralPath $env:SEMANTIC_QWEN_MODEL -PathType Container)) {
            throw "Qwen model missing: $env:SEMANTIC_QWEN_MODEL"
        }
    }
    Assert-YtDlp $root | Out-Null

    if ($engineEnabled -and (Test-LocalPortListening 8792)) { throw "Qwen Worker port 8792 is already in use" }
    if (Test-LocalPortListening 8790) { throw "YTDOWNLOAD port 8790 is already in use" }
    if (Test-LocalPortListening 8791) { throw "SILENCE CUTTER port 8791 is already in use" }

    if ($engineEnabled) {
        Write-LauncherLog $launcherLog "Starting Qwen Worker"
        $qwen_worker = Start-Process -FilePath $silencePython -ArgumentList @("-m", "qwen_worker.supervisor") `
            -WorkingDirectory $silenceRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logs "qwen-worker.stdout.log") `
            -RedirectStandardError (Join-Path $logs "qwen-worker.stderr.log")
        Save-QwenControl $true "STARTING" $qwen_worker
    } else {
        $qwenHealth = "OFF"
        Save-QwenControl $false "OFF" $null
        Write-LauncherLog $launcherLog "Qwen disabled by processing control"
    }
    $env:CONTENTOPS_HEADLESS = "1"
    $ytdownload = Start-Process -FilePath $electron -ArgumentList @(".") -WorkingDirectory $ytdownloadRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "ytdownload.stdout.log") `
        -RedirectStandardError (Join-Path $logs "ytdownload.stderr.log")
    $silence = Start-Process -FilePath $silencePython -ArgumentList @("contentops_process_bridge.py") `
        -WorkingDirectory $silenceRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs "silence.stdout.log") `
        -RedirectStandardError (Join-Path $logs "silence.stderr.log")
    Save-ProductionRuntime

    if ($engineEnabled) {
        $qwenResult = Wait-QwenReady $qwen_worker.Id $QwenTimeoutSeconds `
            -OnState {
                param($state, $health)
                $script:qwenHealth = $state
                Save-ProductionRuntime
                Save-QwenControl $true $state $script:qwen_worker
                $detail = if ($state -eq "ERROR" -and $health.error) { " error=$($health.error)" } else { "" }
                Write-LauncherLog $launcherLog "Qwen: $state$detail"
            }
        if ($qwenResult -ne "READY") { throw "Qwen Worker startup failed: $qwenResult" }
        $qwenHealth = "READY"
        Save-QwenControl $true "READY" $qwen_worker
        Save-ProductionRuntime
    }
    Assert-Healthy $ytdownload "YTDOWNLOAD" "http://127.0.0.1:8790/health"
    Assert-Healthy $silence "SILENCE CUTTER" "http://127.0.0.1:8791/health"
    Assert-SilenceIdentity "http://127.0.0.1:8791/health" $silenceRoot $silencePython

    $bindHost = if ($env:YT_NOTIFI_BIND_HOST) { $env:YT_NOTIFI_BIND_HOST } else { "127.0.0.1" }
    $port = if ($env:YT_NOTIFI_PORT) { [int]$env:YT_NOTIFI_PORT } else { 8787 }
    if ($port -lt 1 -or $port -gt 65535) { throw "Invalid YT_NOTIFI_PORT" }
    if (Test-LocalPortListening $port) { throw "YT_NOTIFI port $port is already in use" }
    $arguments = @("-m", "uvicorn", "app.main:app", "--app-dir", "`"$root`"", "--host", $bindHost, "--port", $port)
    $watcher = Start-Process -FilePath $watcherPython -ArgumentList $arguments -WorkingDirectory $root `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "yt_notifi.stdout.log") `
        -RedirectStandardError (Join-Path $logs "yt_notifi.stderr.log")
    Save-ProductionRuntime
    Assert-Healthy $watcher "YT_NOTIFI" "http://127.0.0.1:$port/health"

    $lanAddress = Get-PrivateLanIPv4
    Write-Host "`nProduction stack READY"
    Write-Host "Dashboard local: http://127.0.0.1:$port/"
    Write-Host "Dashboard LAN:   $(if ($lanAddress) { "http://${lanAddress}:$port/" } else { "unavailable" })"
    Write-LauncherLog $launcherLog "Production stack READY bind=$bindHost port=$port lan=$lanAddress"

    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($service in @($ytdownload, $silence, $watcher)) {
            if ($service.HasExited) { throw "Owned service exited pid=$($service.Id)" }
        }
    }
} catch {
    $exitCode = 1
    $message = Protect-LogText $_.Exception.Message
    Write-Host "PRODUCTION FAILED: $message" -ForegroundColor Red
    Write-LauncherLog $launcherLog "PRODUCTION FAILED message=$message"
} finally {
    Stop-ProductionChildren
    Remove-RuntimeState $runtimePath
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
    Write-LauncherLog $launcherLog "production shutdown complete"
}

exit $exitCode
