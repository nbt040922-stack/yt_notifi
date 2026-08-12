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
$tunnel = $null
$exitCode = 0
$runtime = $null
$launcherStartedAt = Get-ProcessStartUtc (Get-Process -Id $PID)
$startedAt = (Get-Date).ToUniversalTime().ToString("o")

function Save-Runtime {
    $script:runtime = [ordered]@{
        launcher_pid = $PID
        launcher_started_at = $script:launcherStartedAt
        watcher_pid = $(if ($script:watcher) { $script:watcher.Id } else { $null })
        watcher_started_at = $(if ($script:watcher) { Get-ProcessStartUtc $script:watcher } else { $null })
        cloudflared_pid = $(if ($script:tunnel) { $script:tunnel.Id } else { $null })
        cloudflared_started_at = $(if ($script:tunnel) { Get-ProcessStartUtc $script:tunnel } else { $null })
        started_at = $script:startedAt
        tunnel_url = $script:tunnelUrl
    }
    Write-RuntimeState $runtimePath $script:runtime
}

function Start-QuickTunnel([string]$Executable) {
    $stdout = Join-Path $logs "cloudflared.stdout.log"
    $stderr = Join-Path $logs "cloudflared.log"
    Remove-Item $stdout, $stderr -Force -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $Executable -ArgumentList @("tunnel", "--url", "http://127.0.0.1:8787") `
        -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-LauncherLog $launcherLog "cloudflared started pid=$($process.Id)"
    $url = $null
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 30 -and -not $process.HasExited -and -not $url) {
        Start-Sleep -Milliseconds 250
        $text = ((Get-Content $stdout, $stderr -Raw -ErrorAction SilentlyContinue) -join "`n")
        $url = Get-TunnelUrl $text
    }
    if ($process.HasExited -or -not $url) {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        throw "Cloudflare Tunnel failed to start or provide a public URL."
    }
    return [pscustomobject]@{ Process = $process; Url = $url }
}

try {
    Import-DotEnv (Join-Path $root ".env")
    $python = Get-VenvPython $root
    $ytdlp = Find-LauncherExecutable $env:YTDLP_PATH (Join-Path $root "tools\yt-dlp.exe") "yt-dlp"
    $cloudflared = Assert-Cloudflared $root

    Write-Host "====================================="
    Write-Host "YT_NOTIFI"
    Write-Host "=====================================`n"
    Write-Host "yt-dlp        $(if ($ytdlp) { 'OK' } else { 'MISSING' })"
    if (-not $ytdlp) { Write-Host "Polling fallback unavailable" }
    Write-Host "Watcher       STARTING..."

    $watcherOut = Join-Path $logs "watcher.stdout.log"
    $watcherErr = Join-Path $logs "watcher.stderr.log"
    $arguments = @("-m", "uvicorn", "app.main:app", "--app-dir", "`"$root`"", "--host", "127.0.0.1", "--port", "8787")
    $watcher = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root `
        -PassThru -NoNewWindow -RedirectStandardOutput $watcherOut -RedirectStandardError $watcherErr
    Write-LauncherLog $launcherLog "watcher started pid=$($watcher.Id)"
    $script:tunnelUrl = $null
    Save-Runtime

    $health = Wait-LocalHealth $watcher.Id 30
    if ($health -ne "OK") { throw "Watcher health failed: $health. See logs\watcher.stderr.log" }
    Write-Host "Watcher       OK"
    Write-LauncherLog $launcherLog "watcher health PASS"

    Write-Host "Cloudflared   STARTING..."
    $startedTunnel = Start-QuickTunnel $cloudflared
    $tunnel = $startedTunnel.Process
    $script:tunnelUrl = $startedTunnel.Url
    Save-Runtime
    Write-Host "Cloudflared   OK"
    Write-Host "`nLocal:`nhttp://127.0.0.1:8787"
    Write-Host "`nTunnel:`n$script:tunnelUrl"
    $pollInterval = if ($env:POLL_INTERVAL_SECONDS) { $env:POLL_INTERVAL_SECONDS } else { "10" }
    Write-Host "`nPolling:`n$pollInterval seconds"
    Write-Host "`nStatus:`nRUNNING"
    Write-Host "`nPress Ctrl+C to stop."
    Write-LauncherLog $launcherLog "tunnel health PASS url=$script:tunnelUrl pid=$($tunnel.Id)"

    $tunnelRestarts = 0
    while ($true) {
        Start-Sleep -Seconds 1
        if ($watcher.HasExited) { throw "Watcher exited unexpectedly with code $($watcher.ExitCode)." }
        if ($tunnel.HasExited) {
            if ($tunnelRestarts -ge 1) { throw "Cloudflared exited after one restart." }
            $tunnelRestarts++
            Write-Host "Cloudflared   RESTARTING (1/1)..."
            Write-LauncherLog $launcherLog "cloudflared exited; bounded restart"
            $startedTunnel = Start-QuickTunnel $cloudflared
            $tunnel = $startedTunnel.Process
            $script:tunnelUrl = $startedTunnel.Url
            Save-Runtime
            Write-Host "Cloudflared   OK`nTunnel:`n$script:tunnelUrl"
        }
    }
} catch {
    $exitCode = 1
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-LauncherLog $launcherLog "failure=$($_.Exception.GetType().Name) message=$($_.Exception.Message)"
} finally {
    Write-LauncherLog $launcherLog "shutdown requested"
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-OwnedProcess $tunnel.Id (Get-ProcessStartUtc $tunnel) "cloudflared" | Out-Null
    }
    if ($watcher -and -not $watcher.HasExited) {
        Stop-OwnedProcess $watcher.Id (Get-ProcessStartUtc $watcher) "uvicorn app.main:app" | Out-Null
    }
    Remove-RuntimeState $runtimePath
    try { $mutex.ReleaseMutex() } catch {}
    $mutex.Dispose()
    Write-LauncherLog $launcherLog "shutdown complete"
}

exit $exitCode
