$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
. (Join-Path $PSScriptRoot "launcher_lib.ps1")
$runtimeStatus = Get-RuntimeStatus (Join-Path $root "state\runtime.json")
$runtime = $runtimeStatus.Runtime
$watcherRunning = if ($runtime -and $runtime.watcher_pid) {
    Test-RuntimeProcess ([int]$runtime.watcher_pid) ([string]$runtime.watcher_started_at) "uvicorn app.main:app"
} else { $false }
python -m app.status
Write-Host "`nLauncher              $($runtimeStatus.Status)"
if ($runtime) {
    Write-Host "Watcher PID           $($runtime.watcher_pid) $(if ($watcherRunning) { 'RUNNING' } else { 'STALE' })"
}
