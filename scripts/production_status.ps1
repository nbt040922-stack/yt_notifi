$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "launcher_lib.ps1")
Import-DotEnv (Join-Path $root ".env")
$runtime = Read-RuntimeState (Join-Path $root "state\production-runtime.json")

function Show-Service([string]$Label, [string]$Name, [string]$Marker) {
    $running = $false
    if ($runtime) {
        $pidValue = $runtime."${Name}_pid"
        $startedAt = $runtime."${Name}_started_at"
        $running = $pidValue -and (Test-RuntimeProcess ([int]$pidValue) ([string]$startedAt) $Marker)
    }
    Write-Host ("{0,-15} {1}" -f $Label, $(if ($running) { "RUNNING" } else { "STOPPED" }))
}

$port = if ($env:YT_NOTIFI_PORT) { [int]$env:YT_NOTIFI_PORT } else { 8787 }
$lanAddress = Get-PrivateLanIPv4
Show-Service "YT_NOTIFI" "watcher" "uvicorn app.main:app"
Show-Service "YTDOWNLOAD" "ytdownload" "electron.exe"
Show-Service "SILENCE CUTTER" "silence" "contentops_process_bridge.py"
Write-Host "`nDashboard local: http://127.0.0.1:$port/"
Write-Host "Dashboard LAN:   $(if ($lanAddress) { "http://${lanAddress}:$port/" } else { "unavailable" })"
$nas = if ($env:NAS_OUTPUT_ROOT -and (Test-Path -LiteralPath $env:NAS_OUTPUT_ROOT -PathType Container)) { "reachable" } else { "unavailable" }
Write-Host "NAS:             $nas"
