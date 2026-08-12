$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
python -m app.status

. (Join-Path $PSScriptRoot "launcher_lib.ps1")
$runtimeStatus = Get-RuntimeStatus (Join-Path $root "state\runtime.json")
Write-Host "`nLauncher:             $($runtimeStatus.Status)"
if ($runtimeStatus.Runtime) {
    $runtime = $runtimeStatus.Runtime
    $watcherRunning = if ($runtime.watcher_pid) {
        Test-RuntimeProcess ([int]$runtime.watcher_pid) ([string]$runtime.watcher_started_at) "uvicorn app.main:app"
    } else { $false }
    $tunnelRunning = if ($runtime.cloudflared_pid) {
        Test-RuntimeProcess ([int]$runtime.cloudflared_pid) ([string]$runtime.cloudflared_started_at) "cloudflared"
    } else { $false }
    Write-Host "Watcher PID:          $($runtime.watcher_pid) $(if ($watcherRunning) { 'RUNNING' } else { 'STALE' })"
    Write-Host "Tunnel PID:           $($runtime.cloudflared_pid) $(if ($tunnelRunning) { 'RUNNING' } else { 'STALE' })"
    Write-Host "Tunnel URL:           $($runtime.tunnel_url)"
}
