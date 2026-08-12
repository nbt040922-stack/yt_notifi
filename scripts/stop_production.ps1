param([switch]$WhatIf)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "launcher_lib.ps1")
$runtimePath = Join-Path $root "state\production-runtime.json"
$runtime = Read-RuntimeState $runtimePath
if (-not $runtime) {
    Write-Host "Content Ops production is not running."
    exit 0
}

$stopped = @()
$targets = @(
    @("watcher", "uvicorn app.main:app", "YT_NOTIFI"),
    @("silence", "contentops_process_bridge.py", "SILENCE CUTTER"),
    @("ytdownload", "electron.exe", "YTDOWNLOAD")
)
foreach ($target in $targets) {
    $name, $marker, $label = $target
    $pidValue = $runtime."${name}_pid"
    $startedAt = $runtime."${name}_started_at"
    if ($pidValue -and (Stop-OwnedProcessTree ([int]$pidValue) ([string]$startedAt) $marker -WhatIf:$WhatIf)) {
        $stopped += $label
    }
}
if ($runtime.launcher_pid) {
    Stop-OwnedProcess ([int]$runtime.launcher_pid) ([string]$runtime.launcher_started_at) "start_production.ps1" -WhatIf:$WhatIf | Out-Null
}
if (-not $WhatIf) { Remove-RuntimeState $runtimePath }
$verb = if ($WhatIf) { "Would stop" } else { "Stopped" }
Write-Host "$verb owned services: $($stopped -join ', ')"
