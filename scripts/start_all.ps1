$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "launcher_lib.ps1")

$logs = Join-Path $root "logs"
$runtimePath = Join-Path $root "state\runtime.json"
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$launcherLog = Join-Path $logs "launcher.log"
$mutex = Enter-LauncherMutex
if (-not $mutex) {
    Write-Host "YT_NOTIFI is already running"
    exit 2
}

$watcher = $null
$exitCode = 0
$launcherStartedAt = Get-ProcessStartUtc (Get-Process -Id $PID)
$startedAt = (Get-Date).ToUniversalTime().ToString("o")

function Save-Runtime {
    Write-RuntimeState $runtimePath ([ordered]@{
        launcher_pid = $PID
        launcher_started_at = $script:launcherStartedAt
        watcher_pid = $(if ($script:watcher) { $script:watcher.Id } else { $null })
        watcher_started_at = $(if ($script:watcher) { Get-ProcessStartUtc $script:watcher } else { $null })
        started_at = $script:startedAt
    })
}

try {
    Import-DotEnv (Join-Path $root ".env")
    $python = Get-VenvPython $root
    $ytdlp = Assert-YtDlp $root
    $pollInterval = if ($env:POLL_INTERVAL_SECONDS) { $env:POLL_INTERVAL_SECONDS } else { "10" }

    Write-Host "====================================="
    Write-Host "YT_NOTIFI"
    Write-Host "=====================================`n"
    Write-Host "yt-dlp        OK"
    Write-Host "Watcher       STARTING..."

    $watcherOut = Join-Path $logs "watcher.stdout.log"
    $watcherErr = Join-Path $logs "watcher.stderr.log"
    $arguments = @("-m", "uvicorn", "app.main:app", "--app-dir", "`"$root`"", "--host", "127.0.0.1", "--port", "8787")
    $watcher = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
        -PassThru -NoNewWindow -RedirectStandardOutput $watcherOut -RedirectStandardError $watcherErr
    Write-LauncherLog $launcherLog "watcher started pid=$($watcher.Id)"
    Save-Runtime

    $health = Wait-LocalHealth $watcher.Id 30
    if ($health -ne "OK") { throw "Watcher health failed: $health. See logs\watcher.stderr.log" }
    Write-Host "Watcher       OK"
    Write-Host "`nPolling       $pollInterval seconds"
    Write-Host "Dashboard     http://127.0.0.1:8787/"
    Write-Host "Status        RUNNING"
    Write-Host "`nPress Ctrl+C to stop."
    Write-LauncherLog $launcherLog "watcher health PASS"

    while ($true) {
        Start-Sleep -Seconds 1
        if ($watcher.HasExited) { throw "Watcher exited unexpectedly with code $($watcher.ExitCode)." }
    }
} catch {
    $exitCode = 1
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-LauncherLog $launcherLog "failure=$($_.Exception.GetType().Name) message=$($_.Exception.Message)"
} finally {
    Write-LauncherLog $launcherLog "shutdown requested"
    if ($watcher -and -not $watcher.HasExited) {
        Stop-OwnedProcess $watcher.Id (Get-ProcessStartUtc $watcher) "uvicorn app.main:app" | Out-Null
    }
    Remove-RuntimeState $runtimePath
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
    Write-LauncherLog $launcherLog "shutdown complete"
}

exit $exitCode
