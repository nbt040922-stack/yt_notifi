$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
. (Join-Path $PSScriptRoot "launcher_lib.ps1")
$runtimeStatus = Get-RuntimeStatus (Join-Path $root "state\runtime.json")
$runtime = $runtimeStatus.Runtime
$watcherRunning = if ($runtime -and $runtime.watcher_pid) {
    Test-RuntimeProcess ([int]$runtime.watcher_pid) ([string]$runtime.watcher_started_at) "uvicorn app.main:app"
} else { $false }
$tunnelRunning = if ($runtime -and $runtime.cloudflared_pid) {
    Test-RuntimeProcess ([int]$runtime.cloudflared_pid) ([string]$runtime.cloudflared_started_at) "cloudflared"
} else { $false }
$runtimeCallbackActive = $runtimeStatus.Status -eq "RUNNING" -and $tunnelRunning -and $runtime.callback_url
if ($runtimeCallbackActive) {
    $env:YT_NOTIFI_RUNTIME_CALLBACK = [string]$runtimeStatus.Runtime.callback_url
}
python -m app.status
Write-Host "`nLauncher:             $($runtimeStatus.Status)"
if ($runtimeStatus.Runtime) {
    Write-Host "Watcher PID:          $($runtime.watcher_pid) $(if ($watcherRunning) { 'RUNNING' } else { 'STALE' })"
    Write-Host "Tunnel PID:           $($runtime.cloudflared_pid) $(if ($tunnelRunning) { 'RUNNING' } else { 'STALE' })"
    Write-Host "Tunnel URL:           $($runtime.tunnel_url)"
    Write-Host "Cloudflare:           $(if ($tunnelRunning) { 'ONLINE' } else { 'OFFLINE' })"
    Write-Host "Runtime callback:     $(if ($runtimeCallbackActive) { 'ACTIVE' } else { 'STALE' })"
    Write-Host "WebSub callback:      $($runtime.callback_url)"
}
