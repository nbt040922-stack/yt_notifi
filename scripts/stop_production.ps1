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
    @("qwen_worker", "qwen_worker.supervisor", "QWEN WORKER"),
    @("ytdownload", "electron.exe", "YTDOWNLOAD")
)
foreach ($target in $targets) {
    $name, $marker, $label = $target
    $pidProperty = $runtime.PSObject.Properties["${name}_pid"]
    $startedProperty = $runtime.PSObject.Properties["${name}_started_at"]
    $pidValue = $(if ($pidProperty) { $pidProperty.Value } else { $null })
    $startedAt = $(if ($startedProperty) { $startedProperty.Value } else { $null })
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
