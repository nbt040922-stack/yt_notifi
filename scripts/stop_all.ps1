param([switch]$WhatIf)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "launcher_lib.ps1")
$runtimePath = Join-Path $root "state\runtime.json"
$runtime = Read-RuntimeState $runtimePath

if (-not $runtime) {
    Write-Host "YT_NOTIFI is not running"
    exit 0
}

$stopped = @()
if ($runtime.launcher_pid -and (Stop-OwnedProcess ([int]$runtime.launcher_pid) ([string]$runtime.launcher_started_at) "start_all.ps1" -WhatIf:$WhatIf)) {
    $stopped += "launcher"
    if (-not $WhatIf) { Start-Sleep -Seconds 2 }
}
if ($runtime.watcher_pid -and (Stop-OwnedProcess ([int]$runtime.watcher_pid) ([string]$runtime.watcher_started_at) "uvicorn app.main:app" -WhatIf:$WhatIf)) {
    $stopped += "watcher"
}

if (-not $WhatIf) { Remove-RuntimeState $runtimePath }
if ($stopped.Count) {
    Write-Host "Stopped YT_NOTIFI-owned processes: $($stopped -join ', ')"
} else {
    Write-Host "Runtime state is stale; no matching owned processes stopped"
}
