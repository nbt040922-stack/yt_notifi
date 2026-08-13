$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "launcher_lib.ps1")
Import-DotEnv (Join-Path $root ".env")
$runtime = Read-RuntimeState (Join-Path $root "state\production-runtime.json")

function Show-Service([string]$Label, [string]$Name, [string]$Marker) {
    $running = $false
    if ($runtime) {
        $pidProperty = $runtime.PSObject.Properties["${Name}_pid"]
        $startedProperty = $runtime.PSObject.Properties["${Name}_started_at"]
        $pidValue = $(if ($pidProperty) { $pidProperty.Value } else { $null })
        $startedAt = $(if ($startedProperty) { $startedProperty.Value } else { $null })
        $running = $pidValue -and (Test-RuntimeProcess ([int]$pidValue) ([string]$startedAt) $Marker)
    }
    Write-Host ("{0,-15} {1}" -f $Label, $(if ($running) { "RUNNING" } else { "STOPPED" }))
}

$port = if ($env:YT_NOTIFI_PORT) { [int]$env:YT_NOTIFI_PORT } else { 8787 }
$lanAddress = Get-PrivateLanIPv4
Show-Service "YT_NOTIFI" "watcher" "uvicorn app.main:app"
Show-Service "YTDOWNLOAD" "ytdownload" "electron.exe"
Show-Service "SILENCE CUTTER" "silence" "contentops_process_bridge.py"
try {
    $qwen = Invoke-RestMethod "http://127.0.0.1:8792/health" -TimeoutSec 2
    Write-Host ("{0,-15} {1}" -f "QWEN WORKER", $qwen.status)
    Write-Host ("{0,-15} {1}" -f "Qwen Port", "127.0.0.1:8792")
    Write-Host ("{0,-15} {1}" -f "Model Loaded", [bool]$qwen.model_loaded)
    Write-Host ("{0,-15} {1}" -f "Warm", [bool]$qwen.warmed_up)
    if ($qwen.model) { Write-Host ("{0,-15} {1}" -f "Model", $qwen.model) }
    if ($qwen.device) { Write-Host ("{0,-15} {1}" -f "Device", $qwen.device) }
} catch {
    Write-Host ("{0,-15} {1}" -f "QWEN WORKER", "STOPPED")
    Write-Host ("{0,-15} {1}" -f "Qwen Logs", (Join-Path $root "logs\qwen-worker.stderr.log"))
}
Write-Host "`nDashboard local: http://127.0.0.1:$port/"
Write-Host "Dashboard LAN:   $(if ($lanAddress) { "http://${lanAddress}:$port/" } else { "unavailable" })"
$nas = if ($env:NAS_OUTPUT_ROOT -and (Test-Path -LiteralPath $env:NAS_OUTPUT_ROOT -PathType Container)) { "reachable" } else { "unavailable" }
Write-Host "NAS:             $nas"
